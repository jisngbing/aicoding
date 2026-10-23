"""多模型路由与 HTTP 连接池。

池化的是可复用的网络连接和无会话状态的模型客户端，不是用户聊天历史。
角色负责选择模型节点，节点通过环境变量引用密钥；请求消息、回调与会话仍由
调用方传入，防止并发用户串话。同步池线程安全，异步池按事件循环隔离。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from dataclasses import dataclass, field

import httpx
from langchain.chat_models import init_chat_model

from agent.env_utils import get_env
from agent.core.models.model_failover import (ModelCallError, blocked, record_timeout, reset_health,
                                      check_request, HealthCallback, admit, WatchedTransport, WatchedAsyncTransport)


class LoopTransport(httpx.AsyncBaseTransport):
    """让共享 AsyncClient 在每个事件循环中使用独立的真实连接池。

    SSE/Agent 可能在不同线程创建循环，不能把旧循环的 socket、锁交给新循环。
    资源持有者采用异步生成器：asyncio 正常关闭循环时，shutdown_asyncgens
    会在原循环执行 finally，关闭该循环内的连接池，避免跨循环关闭资源。
    """
    def __init__(self, limits, factory=httpx.AsyncHTTPTransport):
        self.limits, self.factory = limits, factory
        self.entries = {}
        self.lock = threading.RLock()

    async def _owner(self, loop, transport):
        try:
            yield transport
        finally:
            try:
                await transport.aclose()
            finally:
                with self.lock:
                    self.entries.pop(loop, None)

    async def handle_async_request(self, request):
        loop = asyncio.get_running_loop()
        # 创建及登记没有 await，原循环内的其他协程不会看到未完成初始化的条目。
        with self.lock:
            entry = self.entries.get(loop)
            if entry is None:
                transport = self.factory(limits=self.limits)
                owner = self._owner(loop, transport)
                self.entries[loop] = entry = (transport, owner)
            else:
                owner = None
        if owner is not None:
            await anext(owner)
        return await entry[0].handle_async_request(request)

    async def aclose(self):
        # 只关闭当前循环拥有的资源；其他循环由自己的生命周期关闭。
        loop = asyncio.get_running_loop()
        with self.lock:
            entry = self.entries.get(loop)
        if entry:
            await entry[1].aclose()


@dataclass(frozen=True)
class Node:
    """节点可代表某厂商的一个模型，或同一厂商的不同账号/推理接入点。"""
    id: str
    provider: str
    model: str
    base_url: str
    api_key: str = field(repr=False)  # 禁止 dataclass 日志意外输出凭据。


def nodes():
    """读取内置厂商和可选自定义节点；配置变更建议重启服务后生效。"""
    definitions = [
        {"id": "qwen", "provider": "qwen", "model": get_env("QWEN_MODEL", get_env("MAIN_MODEL", "qwen3.8-flash")),
         "base_url": get_env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"), "api_key_env": "DASHSCOPE_API_KEY"},
        {"id": "deepseek", "provider": "deepseek", "model": get_env("DEEPSEEK_MODEL", "deepseek-flash"),
         "base_url": get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), "api_key_env": "DEEPSEEK_API_KEY"},
        {"id": "doubao", "provider": "doubao", "model": get_env("DOUBAO_MODEL"),
         "base_url": get_env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"), "api_key_env": "ARK_API_KEY"},
        {"id": "zhipu", "provider": "zhipu", "model": get_env("ZHIPU_MODEL", "glm-4.7-flash"),
         "base_url": get_env("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"), "api_key_env": "ZHIPU_API_KEY"},
    ]
    custom = json.loads(get_env("MODEL_POOL_NODES", "[]"))
    if not isinstance(custom, list):
        raise ValueError("MODEL_POOL_NODES 必须是节点数组")
    definitions.extend(custom)
    result = {}
    for definition in definitions:
        name = definition["id"]
        if name in result:
            raise ValueError("模型节点 ID 重复：" + name)
        result[name] = Node(name, definition["provider"], definition["model"], definition["base_url"],
                            get_env(definition["api_key_env"]).strip())
    return result


_lock = threading.RLock()
_positions = {}
_clients = {}
_models = {}


def select_node(role="main", *, advance=True):
    """按角色轮询已配置的节点，并在同一问题上下文中固定选型。

    例如 MODEL_POOL_MAIN=qwen,deepseek；未配置模型名或密钥的节点不参加轮询。
    显式拼错 ID 会立即报错，不悄悄退回另一厂商。页面读取模型名称不推进轮询。
    """
    if role not in {"main", "subagent", "intent", "evaluation"}:
        raise ValueError("未知模型角色")
    registry = nodes()
    default = get_env("MODEL_POOL_MAIN", "qwen,deepseek,doubao,zhipu") if role != "main" else "qwen,deepseek,doubao,zhipu"
    route = get_env("MODEL_POOL_" + role.upper(), default)
    names = [name.strip() for name in route.split(",") if name.strip()]
    if not names or any(name not in registry for name in names):
        raise ValueError("模型路由包含未知节点：" + role)
    available = [registry[name] for name in names if registry[name].api_key and registry[name].model]
    if not available:
        raise ValueError("模型路由没有可用密钥或模型名称：" + role)
    from agent.core.answer_feedback import current_question
    context = current_question.get()
    with _lock:
        if (context or {}).get("model_pool_timeout"):
            raise ModelCallError()
        # 查固定节点、轮询及写固定节点在同一把锁内完成，避免同一问题并发选到不同节点。
        chosen = (context or {}).get("model_pool_routes", {}).get(role)
        if chosen:
            node = next((node for node in available if node.id == chosen), None)
            if node is None or blocked(node):
                raise ModelCallError()
            return node
        # 正常时优先主模型；主模型超时后只对新问题选择后备节点。
        primary = get_env("MODEL_POOL_PRIMARY", "qwen")
        available = sorted(available, key=lambda item: item.id != primary)
        available = [item for item in available if not blocked(item)]
        if not available:
            raise ModelCallError()
        offset = _positions.get(role, 0) if advance and get_env("MODEL_POOL_STRATEGY", "primary") == "round_robin" else 0
        node = available[offset % len(available)]
        if not admit(node):
            raise ModelCallError()
        if advance:
            _positions[role] = offset + 1
        if context is not None:
            context.setdefault("model_pool_routes", {})[role] = node.id
    return node


def model_name(role="main"):
    """显示名称不要求已经配置密钥；实际调用时才校验节点可用性。"""
    from agent.core.answer_feedback import current_question
    context = current_question.get()
    name = (context or {}).get("model_pool_routes", {}).get(role)
    first = name or get_env("MODEL_POOL_" + role.upper(), get_env("MODEL_POOL_MAIN", "qwen")).split(",")[0].strip()
    node = nodes().get(first)
    return node.model if node else first


def get_model(role="main"):
    """按完整配置缓存模型，HTTP 连接按厂商地址和凭据隔离。

    连接上限限制每个节点的并发 HTTP/1.1 请求数；满池后最多等待 pool_timeout。
    流式请求直到响应关闭才归还连接。不同参数的模型包装器可以共享同一网络池，
    但不会共享调用回调、消息、重试状态。禁止流式输出后自动切换模型重放请求。
    """
    node = select_node(role)
    override = {"intent": "INTENT_MODEL", "evaluation": "EVAL_MODEL"}.get(role)
    actual_model = get_env(override, node.model) if override else node.model
    actual_model = actual_model or node.model
    limits = httpx.Limits(max_connections=int(get_env("MODEL_POOL_MAX_CONNECTIONS", "20")),
                          max_keepalive_connections=int(get_env("MODEL_POOL_MAX_KEEPALIVE", "10")),
                          keepalive_expiry=30)
    if limits.max_connections < 1 or not 0 <= limits.max_keepalive_connections <= limits.max_connections:
        raise ValueError("模型连接池大小配置无效")
    timeout = httpx.Timeout(float(get_env("MODEL_POOL_REQUEST_TIMEOUT", "120")),
                            pool=float(get_env("MODEL_POOL_WAIT_TIMEOUT", "10")))
    if timeout.read <= 0 or timeout.pool <= 0:
        raise ValueError("模型请求及等待超时必须大于0")
    temperature = float(get_env("INTENT_MODEL_TEMPERATURE", "0")) if role == "intent" else (0 if role == "evaluation" else 1.1 if node.provider == "qwen" else .7)
    token_default = "25600" if node.provider == "qwen" else "4096"
    main_limit = get_env("MAIN_MODEL_MAX_TOKENS", token_default) if node.provider == "qwen" else get_env(node.provider.upper() + "_MAX_TOKENS", token_default)
    max_tokens = int(get_env("INTENT_MODEL_MAX_TOKENS", "200")) if role == "intent" else 2200 if role == "evaluation" else int(main_limit)
    # 不把原始密钥放入缓存键、日志或对外接口；轮换密钥会自然生成新的隔离池。
    pool_key = (node.provider, node.base_url, hashlib.sha256(node.api_key.encode()).hexdigest(), str(limits), str(timeout))
    key = (pool_key, node.provider, actual_model, role, temperature, max_tokens)
    with _lock:
        if key in _models:
            return _models[key]
        # 不在运行中淘汰仍可能被流式请求使用的池；配置过多时要求运维重启清理。
        if len(_models) >= 64 or (pool_key not in _clients and len(_clients) >= 64):
            raise RuntimeError("模型池配置数量超过64，请重启服务清理旧配置")
        if pool_key not in _clients:
            notify = lambda: record_timeout(node)
            guard = lambda: check_request(node)
            _clients[pool_key] = (httpx.Client(transport=WatchedTransport(httpx.HTTPTransport(limits=limits), notify, guard=guard, provider=node.provider), timeout=timeout),
                                  httpx.AsyncClient(transport=WatchedAsyncTransport(LoopTransport(limits), notify, guard=guard, provider=node.provider), timeout=timeout))
        sync, asynchronous = _clients[pool_key]
        extra = {"enable_thinking": False} if node.provider == "qwen" else {}
        model = init_chat_model(actual_model, model_provider="openai", api_key=node.api_key, base_url=node.base_url,
            temperature=temperature, max_tokens=max_tokens, streaming=role in {"main", "subagent"},
            http_client=sync, http_async_client=asynchronous, timeout=timeout, max_retries=0,
            extra_body=extra, callbacks=[HealthCallback(node)])
        _models[key] = model
        return model


async def shutdown_model_pool():
    """服务退出时释放连接；调用前应先停止接收新的模型任务。"""
    with _lock:
        clients = list(_clients.values())
        _models.clear()
        _clients.clear()
        _positions.clear()
        reset_health()
    for sync, asynchronous in clients:
        await asyncio.to_thread(sync.close)
        await asynchronous.aclose()
