"""多模型路由和真实客户端的隔离验证，使用模拟 HTTP，不发送收费请求。"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from agent.core.models import model_pool as pool
from agent.core.answer_feedback import current_question


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """每例测试使用独立配置；结束后关闭本例创建的客户端，避免污染其他测试。"""
    asyncio.run(pool.shutdown_model_pool())
    values = {'DASHSCOPE_API_KEY': 'fake-qwen', 'DEEPSEEK_API_KEY': 'fake-deepseek',
              'ARK_API_KEY': 'fake-doubao', 'DOUBAO_MODEL': 'ep-test',
              'MODEL_POOL_MAIN': 'qwen', 'MODEL_POOL_STRATEGY': 'round_robin'}
    monkeypatch.setattr(pool, 'get_env', lambda key, default='': values.get(key, default))
    yield values
    asyncio.run(pool.shutdown_model_pool())


def test_models_reuse_connections_but_keep_role_parameters_separate(monkeypatch):
    factory = lambda *args, **kwargs: SimpleNamespace(model=args[0], **kwargs)
    monkeypatch.setattr(pool, 'init_chat_model', factory)
    main = pool.get_model('main')
    assert pool.get_model('main') is main
    intent = pool.get_model('intent')
    assert intent is not main
    assert intent.http_client is main.http_client
    assert intent.http_async_client is main.http_async_client
    assert intent.max_tokens == 200 and not intent.streaming


def test_round_robin_is_pinned_for_entire_question(isolated):
    isolated['MODEL_POOL_MAIN'] = 'qwen,deepseek'
    selected = []
    for index in range(4):
        token = current_question.set({'question_id': str(index)})
        try:
            node = pool.select_node()
            assert pool.select_node().id == node.id
            assert pool.model_name() == node.model
            selected.append(node.id)
        finally:
            current_question.reset(token)
    assert selected == ['qwen', 'deepseek', 'qwen', 'deepseek']


def test_missing_credentials_do_not_enter_rotation(isolated):
    isolated.update(MODEL_POOL_MAIN='deepseek,doubao', DEEPSEEK_API_KEY='')
    assert pool.select_node().id == 'doubao'
    isolated['ARK_API_KEY'] = ''
    with pytest.raises(ValueError):
        pool.select_node()


def test_doubao_does_not_receive_qwen_private_parameters(isolated, monkeypatch):
    isolated['MODEL_POOL_MAIN'] = 'doubao'
    monkeypatch.setattr(pool, 'init_chat_model', lambda *args, **kwargs: SimpleNamespace(**kwargs))
    model = pool.get_model()
    assert model.base_url == 'https://ark.cn-beijing.volces.com/api/v3'
    assert model.extra_body == {}
    assert model.max_tokens == 4096


def test_key_rotation_creates_new_pool_without_closing_inflight_client(isolated, monkeypatch):
    monkeypatch.setattr(pool, 'init_chat_model', lambda *args, **kwargs: SimpleNamespace(**kwargs))
    old = pool.get_model()
    isolated['DASHSCOPE_API_KEY'] = 'rotated-fake-key'
    new = pool.get_model()
    assert new.http_client is not old.http_client
    assert not old.http_client.is_closed


def test_async_transports_are_closed_in_their_own_loop():
    """两个独立事件循环各建一个池，退出后自动关闭，不能共享旧循环的连接。"""
    created = []
    class MockTransport(httpx.AsyncBaseTransport):
        def __init__(self, **kwargs):
            self.closed = False
            created.append(self)
        async def handle_async_request(self, request):
            return httpx.Response(200, json={'ok': True})
        async def aclose(self):
            self.closed = True
    transport = pool.LoopTransport(httpx.Limits(), factory=MockTransport)
    async def call():
        for _ in range(2):
            response = await transport.handle_async_request(httpx.Request('GET', 'https://test.invalid'))
            await response.aclose()
        assert len(transport.entries) == 1
    asyncio.run(call())
    asyncio.run(call())
    assert len(created) == 2 and all(item.closed for item in created)
    assert not transport.entries


def test_real_langchain_tool_binding_uses_pooled_http(monkeypatch):
    """不是只检查工厂参数：真实 ChatOpenAI 发出请求，验证工具协议与复用。"""
    captured = []
    def handle(request):
        captured.append(request)
        return httpx.Response(200, json={'id': 'mock', 'object': 'chat.completion', 'created': 1,
            'model': 'qwen-test', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': '连接池验证成功'}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    client = httpx.Client(transport=httpx.MockTransport(handle))
    original_factory = pool.init_chat_model
    def factory(*args, **kwargs):
        kwargs['http_client'] = client
        return original_factory(*args, **kwargs)
    monkeypatch.setattr(pool, 'init_chat_model', factory)
    model = pool.get_model('intent')
    bound = model.bind_tools([{'type': 'function', 'function': {'name': 'lookup', 'description': '查询',
                               'parameters': {'type': 'object', 'properties': {}}}}])
    assert bound.invoke('hello').content == '连接池验证成功'
    assert bound.invoke('again').content == '连接池验证成功'
    assert len(captured) == 2
    assert pool.get_model('intent') is model
    client.close()


def test_cached_model_streams_in_two_separate_async_loops(monkeypatch):
    """复现真实工作流：同一个模型跨两个工作线程循环使用，流式响应不能串循环。"""
    import json
    def handle(request):
        events = []
        for delta, finish in [({'content': 'hello'}, None), ({'content': ' world'}, None), ({}, 'stop')]:
            events.append('data: ' + json.dumps({'id': 'stream-test', 'model': 'qwen-test', 'created': 1,
                'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              content=(''.join(events) + 'data: [DONE]\n\n').encode())
    original_transport = pool.LoopTransport
    monkeypatch.setattr(pool, 'LoopTransport', lambda limits: original_transport(limits,
        factory=lambda **kwargs: httpx.MockTransport(handle)))
    model = pool.get_model('main')
    async def run():
        return ''.join([chunk.content async for chunk in model.astream('hello')])
    assert asyncio.run(run()) == 'hello world'
    assert asyncio.run(run()) == 'hello world'


def test_intent_wrapper_does_not_freeze_first_routed_model(isolated, monkeypatch):
    """分类包装器不能再缓存第一个模型，否则修改角色路由或轮询都不会生效。"""
    from agent.core import task_intent
    isolated['MODEL_POOL_INTENT'] = 'qwen,deepseek'
    def create():
        selected = pool.select_node('intent')
        return SimpleNamespace(with_structured_output=lambda *a, **k: selected.id)
    monkeypatch.setattr(task_intent, 'make_intent_model', create)
    assert [task_intent._make_structured_intent_model() for _ in range(3)] == ['qwen', 'deepseek', 'qwen']
