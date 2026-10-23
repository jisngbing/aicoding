"""验证厂商错误码、固定退避次数和限流不熔断，所有等待均为模拟。"""
import asyncio
from unittest.mock import Mock

import httpx
import pytest
from openai import RateLimitError

from agent.core.models import model_errors as errors, model_failover as health, model_pool as pool
from agent.core.models.model_error_codes import ModelErrorCode, ERROR_DEFINITIONS


@pytest.mark.parametrize('provider,status,code,message,category', [
    ('qwen', 429, 'Throttling.RateQuota', '', 'rate_limit'),
    ('qwen', 429, 'insufficient_quota', '', 'rate_limit'),
    ('qwen', 429, 'Throttling.AllocationQuota', '', 'rate_limit'),
    ('qwen', 403, 'AllocationQuota.FreeTierOnly', '', 'quota'),
    ('qwen', 403, 'Arrearage', '', 'quota'),
    ('qwen', 429, 'Throttling.ResourceExhausted', 'week allocated quota exceeded', 'quota'),
    ('deepseek', 402, 'invalid_request_error', 'Insufficient Balance', 'quota'),
    ('deepseek', 429, '', '', 'rate_limit'),
    ('deepseek', 503, '', '', 'call_failure'),
    ('doubao', 403, 'AccountOverdueError', '', 'quota'),
    ('doubao', 429, 'AccountQuotaExceeded', '', 'quota'),
    ('doubao', 429, 'QuotaExceeded', 'Your free trial quota is exhausted', 'quota'),
    ('doubao', 429, 'QuotaExceeded', 'The queued task limit was exceeded', 'rate_limit'),
    ('doubao', 429, 'ModelAccountTpdRateLimitExceeded', '', 'quota'),
    ('doubao', 429, 'AccountRateLimitExceeded', '', 'rate_limit'),
    ('zhipu', 429, '1113', '', 'quota'),
    ('zhipu', 429, '1302', '', 'rate_limit'),
    ('zhipu', 429, '1305', '', 'rate_limit'),
    ('zhipu', 429, '1310', '', 'quota'),
    ('zhipu', 400, '1261', '', 'context_limit'),
    ('qwen', 401, 'InvalidApiKey', '', 'auth'),
    ('deepseek', 400, 'context_length_exceeded', '', 'context_limit'),
    ('deepseek', 422, 'invalid_parameter', '', 'bad_request'),
])
def test_vendor_code_precedes_http_status(provider, status, code, message, category):
    assert errors.classify(status, {'error': {'code': code, 'message': message}}, provider).category == category


def test_system_error_code_is_independent_of_vendor_code():
    info = errors.classify(429, {'error': {'code': '1302'}}, 'zhipu')
    assert info.error_code == ModelErrorCode.RATE_LIMITED.value
    assert info.code == '1302'
    assert info.retryable and not info.trip_circuit
    quota = errors.classify(429, {'error': {'code': '1113'}}, 'zhipu')
    assert quota.error_code == ModelErrorCode.QUOTA_EXHAUSTED.value
    assert quota.trip_circuit and not quota.retryable
    assert health.ModelCallError().info.error_code == ModelErrorCode.CALL_FAILED.value


def test_all_error_definitions_have_unique_codes_and_safe_messages():
    assert len({definition.code.value for definition in ERROR_DEFINITIONS.values()}) == len(ERROR_DEFINITIONS)
    info = errors.classify(400, {'error': {'message': 'api_key=private-test-value'}})
    assert 'private-test-value' not in info.message


def limited():
    return httpx.Response(429, json={'error': {'code': 'Throttling.RateQuota'}})


def test_sync_limited_request_retries_three_times_then_returns_error(monkeypatch):
    sleeps, bodies = [], []
    monkeypatch.setattr(health.time, 'sleep', sleeps.append)
    def handle(request):
        bodies.append(request.read())
        return limited()
    notify = Mock()
    transport = health.WatchedTransport(httpx.MockTransport(handle), notify, provider='qwen')
    with httpx.Client(transport=transport) as client:
        response = client.post('https://test.invalid', json={'messages': ['same request']})
    assert response.status_code == 429
    assert sleeps == [5, 15, 30] and len(bodies) == 4
    assert len(set(bodies)) == 1  # 同模型、同参数，不在重试过程中换模型或拼接输出。
    notify.assert_not_called()


def test_success_during_backoff_stops_retrying(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(health.time, 'sleep', sleeps.append)
    def handle(request):
        calls.append(1)
        return limited() if len(calls) < 3 else httpx.Response(200, json={'ok': True})
    notify = Mock()
    with httpx.Client(transport=health.WatchedTransport(httpx.MockTransport(handle), notify)) as client:
        assert client.get('https://test.invalid').status_code == 200
    assert sleeps == [5, 15] and len(calls) == 3
    notify.assert_not_called()


def test_async_rate_limit_uses_async_backoff(monkeypatch):
    sleeps = []
    async def sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(health.asyncio, 'sleep', sleep)
    notify = Mock()
    async def run():
        transport = health.WatchedAsyncTransport(httpx.MockTransport(lambda request: limited()), notify)
        async with httpx.AsyncClient(transport=transport) as client:
            assert (await client.post('https://test.invalid', json={'messages': []})).status_code == 429
    asyncio.run(run())
    assert sleeps == [5, 15, 30]
    notify.assert_not_called()


@pytest.mark.parametrize('status,code,should_switch', [(402, '', True), (503, '', True),
                                                   (401, 'InvalidApiKey', False), (400, 'context_length_exceeded', False)])
def test_non_limit_errors_are_not_retried(monkeypatch, status, code, should_switch):
    sleeps, calls = [], []
    monkeypatch.setattr(health.time, 'sleep', sleeps.append)
    def handle(request):
        calls.append(1)
        return httpx.Response(status, json={'error': {'code': code}})
    notify = Mock()
    with httpx.Client(transport=health.WatchedTransport(httpx.MockTransport(handle), notify)) as client:
        client.get('https://test.invalid')
    assert not sleeps and len(calls) == 1
    assert notify.call_count == int(should_switch)


def test_real_sdk_rate_limit_exhaustion_does_not_switch_primary(monkeypatch):
    """真实 ChatOpenAI + 错误响应，防止 SDK 回调把已耗尽重试的限流误触发熔断。"""
    asyncio.run(pool.shutdown_model_pool())
    values = {'DASHSCOPE_API_KEY': 'fake', 'MODEL_POOL_MAIN': 'qwen'}
    monkeypatch.setattr(pool, 'get_env', lambda key, default='': values.get(key, default))
    sleeps = []
    monkeypatch.setattr(health.time, 'sleep', sleeps.append)
    monkeypatch.setattr(pool.httpx, 'HTTPTransport', lambda **kwargs: httpx.MockTransport(lambda request: limited()))
    try:
        model = pool.get_model('intent')
        with pytest.raises(RateLimitError):
            model.invoke('hello')
        assert sleeps == [5, 15, 30]
        assert not health.blocked(pool.nodes()['qwen'])
        assert pool.select_node('intent').id == 'qwen'
    finally:
        asyncio.run(pool.shutdown_model_pool())
