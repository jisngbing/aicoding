"""模型故障重试的行为验证：模拟所有网络请求和等待，不调用真实厂商。"""
import asyncio
from unittest.mock import Mock

import httpx
import pytest

from agent.core.answer_feedback import current_question
from agent.core.models import model_failover as health


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('failure', ['timeout', 'connection', 'server', 'http_timeout'])
def test_failure_retries_same_request_then_records_terminal_error(monkeypatch, asynchronous, failure):
    """同步和异步路径均最多请求四次；中间失败不熔断，最终记录重试数。"""
    waits, bodies = [], []
    notify = Mock()
    monkeypatch.setattr(health.time, 'sleep', waits.append)

    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(health.asyncio, 'sleep', sleep)

    def handle(request):
        notify.assert_not_called()
        bodies.append(request.read())
        if failure == 'timeout':
            raise httpx.ReadTimeout('fake', request=request)
        if failure == 'connection':
            raise httpx.ConnectError('fake', request=request)
        return httpx.Response(503 if failure == 'server' else 408, json={'error': {'code': 'service_error'}})

    async def run_async():
        transport = health.WatchedAsyncTransport(httpx.MockTransport(handle), notify)
        async with httpx.AsyncClient(transport=transport) as client:
            return await client.post('https://test.invalid', json={'messages': ['same question']})

    def run():
        if asynchronous:
            return asyncio.run(run_async())
        transport = health.WatchedTransport(httpx.MockTransport(handle), notify)
        with httpx.Client(transport=transport) as client:
            return client.post('https://test.invalid', json={'messages': ['same question']})

    token = current_question.set({'question_id': 'failure-retries'})
    try:
        if failure in ('timeout', 'connection'):
            with pytest.raises(httpx.RequestError):
                run()
        else:
            assert run().status_code in (408, 503)
        info = current_question.get()['model_pool_error']
        assert info.error_code == 'MODEL_CALL_FAILED' and info.retries == 3
        assert info.message == '调用模型异常，重试3次仍失败，请稍后重试'
    finally:
        current_question.reset(token)
    assert waits == [5, 15, 30] and len(bodies) == 4
    assert len(set(bodies)) == 1
    notify.assert_called_once()


@pytest.mark.parametrize('asynchronous', [False, True])
def test_transient_failure_recovers_without_circuit_or_error_context(monkeypatch, asynchronous):
    """第二次请求恢复后不遗留本轮错误或熔断状态，后续模型步骤可继续调用。"""
    waits, calls = [], []
    notify = Mock()
    monkeypatch.setattr(health.time, 'sleep', waits.append)
    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(health.asyncio, 'sleep', sleep)
    def handle(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError('fake', request=request)
        return httpx.Response(200, json={'ok': True})
    async def run_async():
        async with httpx.AsyncClient(transport=health.WatchedAsyncTransport(httpx.MockTransport(handle), notify)) as client:
            return await client.get('https://test.invalid')
    token = current_question.set({'question_id': 'recover'})
    try:
        if asynchronous:
            response = asyncio.run(run_async())
        else:
            with httpx.Client(transport=health.WatchedTransport(httpx.MockTransport(handle), notify)) as client:
                response = client.get('https://test.invalid')
        assert response.status_code == 200
        assert 'model_pool_error' not in current_question.get()
    finally:
        current_question.reset(token)
    assert waits == [5] and len(calls) == 2
    notify.assert_not_called()


def test_mixed_rate_limit_and_server_failure_share_one_retry_budget(monkeypatch):
    """限流与模型故障交替也只能重试三次，不能按错误类别叠加次数。"""
    waits, calls = [], []
    monkeypatch.setattr(health.time, 'sleep', waits.append)
    def handle(request):
        calls.append(1)
        return httpx.Response(429 if len(calls) % 2 else 503, json={'error': {'code': 'fake'}})
    notify = Mock()
    with httpx.Client(transport=health.WatchedTransport(httpx.MockTransport(handle), notify)) as client:
        assert client.get('https://test.invalid').status_code == 503
    assert waits == [5, 15, 30] and len(calls) == 4
    notify.assert_called_once()
