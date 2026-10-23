"""模型调用故障和限流按同节点退避重试；耗尽后返回错误，故障节点影响后续选择。

已交给调用方的流式响应不重放，避免已展示内容和工具动作重复执行。
"""
import hashlib
import asyncio
import threading
import time
import uuid
from contextvars import ContextVar

import httpx
from openai import APITimeoutError
from agent.env_utils import get_env
from langchain_core.callbacks import BaseCallbackHandler
from dataclasses import replace
from agent.core.models.model_errors import (ErrorInfo, ModelRequestError, RETRY_DELAYS, classify,
                                     exception_info, remember_error)

_lock = threading.RLock()
_states = {}
_tickets = ContextVar('model_probe_tickets', default=None)


def recovery_seconds():
    """使用单调时钟计算等待期，避免系统校时改变熔断时长。"""
    value = float(get_env('MODEL_POOL_RECOVERY_SECONDS', '60'))
    if value <= 0:
        raise ValueError('MODEL_POOL_RECOVERY_SECONDS 必须大于0')
    return value


class ModelCallError(ModelRequestError):
    """统一对外错误文案，不暴露厂商地址、密钥或 SDK 内部异常。"""
    def __init__(self):
        super().__init__(ErrorInfo('call_failure'))


def identity(node):
    """相同地址和凭据共用健康状态，避免换别名后重新打到同一故障连接池。"""
    return node.base_url, hashlib.sha256(node.api_key.encode()).hexdigest()


def blocked(node):
    with _lock:
        key = identity(node)
        state = _states.get(key)
        if not state:
            return False
        if state['state'] == 'open':
            return time.monotonic() < state['until']
        if time.monotonic() >= state['until']:
            # 探测任务中断/未真正发起请求时，租约到期按探测失败处理，重新等待。
            _states[key] = {'state': 'open', 'until': time.monotonic() + recovery_seconds()}
            return True
        return not owns_probe(key, state)


def owns_probe(key, state):
    """探测票据同时绑定问题，避免复用上下文时被另一个问题误用。"""
    from agent.core.answer_feedback import current_question
    question = (current_question.get() or {}).get('question_id')
    return (_tickets.get() or {}).get(key) == (state['ticket'], question)


def admit(node):
    """到期后原子认领唯一探测名额；其他并发请求继续走备用模型。"""
    with _lock:
        if blocked(node):
            return False
        key = identity(node)
        state = _states.get(key)
        if state and state['state'] == 'open':
            ticket = uuid.uuid4().hex
            # 半开探测包含首次调用及三次重试；租约须覆盖全部请求超时和退避，
            # 防止尚在合法重试的探测被其他请求误判为失效。
            lease = (len(RETRY_DELAYS) + 1) * (
                float(get_env('MODEL_POOL_REQUEST_TIMEOUT', '120'))
                + float(get_env('MODEL_POOL_WAIT_TIMEOUT', '10'))
            ) + sum(RETRY_DELAYS)
            _states[key] = {'state': 'half_open', 'ticket': ticket, 'until': time.monotonic() + lease}
            from agent.core.answer_feedback import current_question
            question = (current_question.get() or {}).get('question_id')
            _tickets.set({**(_tickets.get() or {}), key: (ticket, question)})
        return True


def record_success(node):
    """只有本次探测请求完整读取响应，才能恢复主模型正常流量。"""
    key = identity(node)
    with _lock:
        state = _states.get(key)
        if state and state['state'] == 'half_open' and owns_probe(key, state):
            _states.pop(key, None)


def check_request(node):
    """缓存模型再次调用前也检查状态，避免 Agent 内部吞掉异常后重复请求故障模型。"""
    from agent.core.answer_feedback import current_question
    info = (current_question.get() or {}).get('model_pool_error')
    if info:
        raise ModelRequestError(info)
    if (current_question.get() or {}).get('model_pool_timeout') or not admit(node):
        raise ModelCallError()


class HealthCallback(BaseCallbackHandler):
    """等待 SDK 完成响应解析再确认探测成功，而不是仅收到 HTTP 200 就恢复。"""
    run_inline = True  # 与请求使用同一上下文，保留探测票据及问题 ID。

    def __init__(self, node):
        self.node = node

    def on_llm_end(self, response, **kwargs):
        record_success(self.node)

    def on_llm_error(self, error, **kwargs):
        info = exception_info(error, self.node.provider) or ErrorInfo('call_failure')
        remember_error(info)
        if info.trip_circuit:
            if not blocked(self.node):
                record_timeout(self.node)
        else:
            # 限流/参数问题不能产生新的熔断；探测受限时释放名额，供后续调用再探测。
            key = identity(self.node)
            with _lock:
                state = _states.get(key)
                if state and state['state'] == 'half_open' and owns_probe(key, state):
                    _states[key] = {'state': 'open', 'until': time.monotonic()}


def record_timeout(node):
    """进入等待期；探测失败也重新计时，不在同一次调用内更换模型。"""
    with _lock:
        _states[identity(node)] = {'state': 'open', 'until': time.monotonic() + recovery_seconds()}
    from agent.core.answer_feedback import current_question
    context = current_question.get()
    if context is not None:
        # 上下文里的字典会传播到本轮 Agent 的异步任务，防止其他步骤继续换模型执行。
        context['model_pool_timeout'] = True


def reset_health():
    """供退出清理、测试及运维显式恢复使用，不在普通请求里自动调用。"""
    with _lock:
        _states.clear()
        _tickets.set(None)


def is_model_timeout(exc):
    from agent.core.answer_feedback import current_question
    if (current_question.get() or {}).get('model_pool_timeout'):
        return True
    # SDK 会把 HTTPX 超时包装成 APITimeoutError；沿异常链识别，兼容运行图再包装。
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (APITimeoutError, ModelCallError)):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class WatchedStream(httpx.SyncByteStream):
    def __init__(self, stream, notify, success=lambda: None):
        self.stream, self.notify, self.success = stream, notify, success

    def __iter__(self):
        try:
            yield from self.stream
        except httpx.RequestError:
            self.notify()
            raise
        else:
            self.success()

    def close(self):
        self.stream.close()


class WatchedAsyncStream(httpx.AsyncByteStream):
    def __init__(self, stream, notify, success=lambda: None):
        self.stream, self.notify, self.success = stream, notify, success

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
        except httpx.RequestError:
            self.notify()
            raise
        else:
            self.success()

    async def aclose(self):
        await self.stream.aclose()


class WatchedTransport(httpx.BaseTransport):
    """在交付响应前重试网络/服务故障；交付后的 SSE 读取异常只标记故障。"""
    def __init__(self, delegate, notify, success=lambda: None, guard=lambda: None, provider=''):
        self.delegate, self.notify, self.success, self.guard = delegate, notify, success, guard
        self.provider = provider

    def handle_request(self, request):
        self.guard()
        body = request.read()  # JSON 请求体缓存后可安全重发；绝不重放已经开始输出的 SSE。
        for attempt in range(len(RETRY_DELAYS) + 1):
            request.stream = httpx.ByteStream(body)
            response = None
            try:
                response = self.delegate.handle_request(request)
                if response.status_code >= 400:
                    response.read()
            except httpx.RequestError:
                if response is not None:
                    response.close()
                if attempt < len(RETRY_DELAYS):
                    # 中间失败不能写入本轮错误或熔断，否则会阻断后面的合法重试。
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                self.notify()
                remember_error(ErrorInfo('call_failure', retries=attempt))
                raise
            if response.status_code < 400:
                response.stream = WatchedStream(response.stream, self.notify, self.success)
                return response
            try:
                data = response.json()
            except ValueError:
                data = {}
            info = classify(response.status_code, data, self.provider)
            if info.retryable and attempt < len(RETRY_DELAYS):
                # 先关闭失败响应归还连接，再等待；不能占着池连接休眠。
                response.close()
                time.sleep(RETRY_DELAYS[attempt])
                continue
            info = replace(info, retries=attempt)
            if info.trip_circuit:
                self.notify()
            remember_error(info)
            return response  # SDK 解析原错误码；UI 只展示归一化的安全文案。

    def close(self):
        self.delegate.close()


class WatchedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, delegate, notify, success=lambda: None, guard=lambda: None, provider=''):
        self.delegate, self.notify, self.success, self.guard = delegate, notify, success, guard
        self.provider = provider

    async def handle_async_request(self, request):
        self.guard()
        body = await request.aread()
        for attempt in range(len(RETRY_DELAYS) + 1):
            request.stream = httpx.ByteStream(body)
            response = None
            try:
                response = await self.delegate.handle_async_request(request)
                if response.status_code >= 400:
                    await response.aread()
            except httpx.RequestError:
                if response is not None:
                    await response.aclose()
                if attempt < len(RETRY_DELAYS):
                    # CancelledError 不属于 RequestError，取消任务时立即退出退避。
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                self.notify()
                remember_error(ErrorInfo('call_failure', retries=attempt))
                raise
            if response.status_code < 400:
                response.stream = WatchedAsyncStream(response.stream, self.notify, self.success)
                return response
            try:
                data = response.json()
            except ValueError:
                data = {}
            info = classify(response.status_code, data, self.provider)
            if info.retryable and attempt < len(RETRY_DELAYS):
                await response.aclose()
                # 异步退避可取消，不阻塞 FastAPI/SSE 事件循环。
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            info = replace(info, retries=attempt)
            if info.trip_circuit:
                self.notify()
            remember_error(info)
            return response

    async def aclose(self):
        await self.delegate.aclose()
