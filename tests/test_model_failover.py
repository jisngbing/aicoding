"""验证故障重试耗尽后才切换后续请求，当前问题始终固定同一个模型。"""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

from agent.core.models import model_pool as pool
from agent.core.models import model_failover as health
from agent.core.models.model_errors import ModelRequestError
from agent.core.answer_feedback import current_question


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    asyncio.run(pool.shutdown_model_pool())
    values = {'DASHSCOPE_API_KEY': 'fake-qwen', 'DEEPSEEK_API_KEY': 'fake-deepseek',
              'ARK_API_KEY': 'fake-doubao', 'DOUBAO_MODEL': 'ep-test',
              'MODEL_POOL_MAIN': 'qwen,deepseek,doubao', 'MODEL_POOL_PRIMARY': 'qwen'}
    monkeypatch.setattr(pool, 'get_env', lambda key, default='': values.get(key, default))
    monkeypatch.setattr(health.time, 'sleep', lambda seconds: None)
    yield
    asyncio.run(pool.shutdown_model_pool())


def test_primary_is_preferred_until_timeout_then_next_request_moves():
    for _ in range(3):
        assert pool.select_node().id == 'qwen'
    token = current_question.set({'question_id': 'first'})
    try:
        node = pool.select_node()
        health.record_timeout(node)
        with pytest.raises(health.ModelCallError, match='调用模型异常'):
            pool.select_node()  # 本轮不允许内部降级或重做。
    finally:
        current_question.reset(token)
    assert pool.select_node().id == 'deepseek'
    health.record_timeout(pool.select_node())
    assert pool.select_node().id == 'doubao'


def test_no_backup_returns_clear_error():
    for node in pool.nodes().values():
        health.record_timeout(node)
    with pytest.raises(health.ModelCallError):
        pool.select_node()


def test_sdk_retries_are_disabled(monkeypatch):
    monkeypatch.setattr(pool, 'init_chat_model', lambda *a, **k: NS(**k))
    assert pool.get_model().max_retries == 0


def test_sync_timeout_notifies_once_after_retry_exhaustion():
    def fail(request):
        raise httpx.ReadTimeout('fake timeout', request=request)
    notify = Mock()
    transport = health.WatchedTransport(httpx.MockTransport(fail), notify)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(httpx.ReadTimeout):
            client.get('https://test.invalid')
    notify.assert_called_once()


def test_async_partial_stream_timeout_marks_node():
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'first token'
            raise httpx.ReadTimeout('stream stalled')
        async def aclose(self):
            pass
    notify = Mock()
    async def run():
        delegate = httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))
        async with httpx.AsyncClient(transport=health.WatchedAsyncTransport(delegate, notify)) as client:
            async with client.stream('GET', 'https://test.invalid') as response:
                with pytest.raises(httpx.ReadTimeout):
                    chunks = [chunk async for chunk in response.aiter_bytes()]
    asyncio.run(run())
    notify.assert_called_once()


def test_classifier_timeout_does_not_fall_back_to_keywords(monkeypatch):
    from agent.core import task_intent
    from openai import APITimeoutError
    monkeypatch.setattr(task_intent, '_classify_by_model', Mock(side_effect=APITimeoutError(request=httpx.Request('POST', 'https://test.invalid'))))
    keyword = Mock()
    monkeypatch.setattr(task_intent, '_classify_by_keyword_backup', keyword)
    with pytest.raises((health.ModelCallError, ModelRequestError)):
        task_intent.classify_task_kind('解释一下 Python 列表是什么')
    keyword.assert_not_called()


def test_cooldown_half_open_one_probe_then_recover(monkeypatch):
    """不用真实等待：推进单调时钟，确认等待期、唯一探测和恢复主模型。"""
    from contextvars import Context
    clock = [100.0]
    monkeypatch.setattr(health.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(health, 'get_env', lambda name, default='': '30' if name == 'MODEL_POOL_RECOVERY_SECONDS' else default)
    node = pool.select_node()
    health.record_timeout(node)
    assert pool.select_node().id == 'deepseek'
    clock[0] += 30
    probe_context = Context()
    assert probe_context.run(pool.select_node).id == 'qwen'
    assert Context().run(pool.select_node).id == 'deepseek'
    # 别的旧请求返回成功不能误关熔断器。
    Context().run(health.record_success, node)
    assert Context().run(pool.select_node).id == 'deepseek'
    probe_context.run(health.record_success, node)
    assert Context().run(pool.select_node).id == 'qwen'


def test_failed_probe_starts_new_wait_period(monkeypatch):
    from contextvars import Context
    clock = [100.0]
    monkeypatch.setattr(health.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(health, 'get_env', lambda name, default='': '20' if name == 'MODEL_POOL_RECOVERY_SECONDS' else default)
    node = pool.select_node()
    health.record_timeout(node)
    clock[0] += 20
    probe = Context()
    assert probe.run(pool.select_node).id == 'qwen'
    probe.run(health.record_timeout, node)
    assert Context().run(pool.select_node).id == 'deepseek'
    clock[0] += 19
    assert Context().run(pool.select_node).id == 'deepseek'
    clock[0] += 1
    assert Context().run(pool.select_node).id == 'qwen'


def test_real_sdk_timeout_then_backup_then_primary_recovers(monkeypatch):
    """真实 SDK + 模拟 HTTP 验证整条链路，确认同节点重试耗尽后才允许下个问题切备用。"""
    from contextvars import Context
    from openai import APITimeoutError
    clock, calls = [100.0], []
    monkeypatch.setattr(health.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(health, 'get_env', lambda name, default='': '60' if name == 'MODEL_POOL_RECOVERY_SECONDS' else default)
    def handle(request):
        calls.append(request.url.host)
        if len(calls) <= 4:
            raise httpx.ReadTimeout('simulated timeout', request=request)
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 1,
            'model': 'test', 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': 'ok'}}]})
    monkeypatch.setattr(pool.httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(handle))
    def invoke(question):
        token = current_question.set({'question_id': question})
        try:
            return pool.get_model('intent').invoke('test').content
        finally:
            current_question.reset(token)
    with pytest.raises(APITimeoutError):
        Context().run(invoke, 'first')
    assert calls == ['dashscope.aliyuncs.com'] * 4
    assert Context().run(invoke, 'second') == 'ok'
    assert calls[-1] == 'api.deepseek.com'
    clock[0] += 60
    assert Context().run(invoke, 'probe') == 'ok'
    assert calls[-1] == 'dashscope.aliyuncs.com'
    assert Context().run(invoke, 'normal') == 'ok'
    assert calls[-1] == 'dashscope.aliyuncs.com'
