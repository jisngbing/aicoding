import importlib
import json

import pytest
import requests

search = importlib.import_module("agent.tools.web_search")


class Response:
    def __init__(self, data=None, status=200, raw=None):
        self.status_code = status
        self.headers = {"Retry-After": "0"}
        self.raw = raw if raw is not None else json.dumps(data).encode()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, size):
        for start in range(0, len(self.raw), size):
            yield self.raw[start:start + size]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(search, "load_environment", lambda: None)
    monkeypatch.setattr(search, "get_runtime_thread_id", lambda: None)
    monkeypatch.setenv("ZHIPU_API_KEY", "test-private-id.test-secret")
    monkeypatch.delenv("ZHIPU_SEARCH_ENGINE", raising=False)
    monkeypatch.delenv("ZHIPU_SEARCH_TIMEOUT", raising=False)
    monkeypatch.setattr(search.time, "sleep", lambda delay: None)
    monkeypatch.setattr(search.requests, "post", lambda *a, **kw: pytest.fail("Unexpected network request"))


def invoke(**args):
    return json.loads(search.web_search.invoke(args))


def test_query_only_compatibility_and_source_deduplication(monkeypatch):
    response = Response({"search_result": [
        {"title": "Python", "content": "x" * 4000, "link": "https://docs.python.org/3/#a", "media": "Python", "publish_date": "2026-10-01"},
        {"title": "duplicate", "link": "https://docs.python.org/3/#b"},
        {"title": "invalid", "link": "javascript:alert(1)"},
        {"title": "test-private-id.test-secret", "link": "https://example.com"},
    ]})
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return response
    monkeypatch.setattr(search.requests, "post", post)
    output = invoke(query=" Python\n docs ")
    assert output["ok"] and output["count"] == 2
    assert output["query"] == "Python docs"
    assert output["results"][0]["published_at"] == "2026-10-01"
    assert len(output["results"][0]["content"]) == 1800
    assert "test-secret" not in json.dumps(output)
    assert calls[0]["json"]["count"] == 5
    assert calls[0]["timeout"] == (5, 20)
    assert calls[0]["allow_redirects"] is False
    assert response.closed


@pytest.mark.parametrize("args,code", [
    ({"query": ""}, "invalid_query"),
    ({"query": "x" * 71}, "invalid_query"),
    ({"query": "x", "count": 11}, "invalid_options"),
    ({"query": "x", "recency": "yesterday"}, "invalid_options"),
    ({"query": "x", "domain": "https://example.com/a"}, "invalid_domain"),
    ({"query": "test-private-id.test-secret"}, "sensitive_query"),
])
def test_validation_before_network(args, code):
    assert invoke(**args)["error_code"] == code


def test_filters_and_event_lifecycle(monkeypatch):
    events, payloads = [], []
    monkeypatch.setattr(search, "get_runtime_thread_id", lambda: "thread")
    monkeypatch.setattr(search, "record_event", lambda *a, **kw: events.append(kw))
    def post(*a, **kw):
        payloads.append(kw["json"])
        return Response({"search_result": []})
    monkeypatch.setattr(search.requests, "post", post)
    assert invoke(query="python", count=2, domain="DOCS.PYTHON.ORG", recency="oneWeek")["count"] == 0
    assert payloads[0]["search_domain_filter"] == "docs.python.org"
    assert payloads[0]["search_recency_filter"] == "oneWeek"
    assert [e["status"] for e in events] == ["in_progress", "completed"]


def test_rate_limit_retry_once_then_success(monkeypatch):
    responses = [Response(status=429), Response({"search_result": []})]
    calls = []
    def post(*a, **kw):
        calls.append(kw)
        return responses[len(calls) - 1]
    monkeypatch.setattr(search.requests, "post", post)
    assert invoke(query="python")["ok"]
    assert len(calls) == 2 and all(r.closed for r in responses)


@pytest.mark.parametrize("status,code,calls_expected", [(401, "authentication", 1), (403, "authentication", 1), (429, "rate_limit", 2), (503, "http_error", 2)])
def test_http_errors_without_secret_disclosure(monkeypatch, status, code, calls_expected):
    calls = []
    def post(*a, **kw):
        calls.append(kw)
        return Response({"error": "test-private-id.test-secret"}, status=status)
    monkeypatch.setattr(search.requests, "post", post)
    output = invoke(query="python")
    assert output["ok"] is False and output["error_code"] == code
    assert len(calls) == calls_expected
    assert "test-secret" not in json.dumps(output)


@pytest.mark.parametrize("exc,code", [(requests.Timeout("test-secret"), "timeout"), (requests.ConnectionError("test-secret"), "connection")])
def test_network_errors_are_safe_and_not_retried(monkeypatch, exc, code):
    calls = []
    def post(*a, **kw):
        calls.append(kw)
        raise exc
    monkeypatch.setattr(search.requests, "post", post)
    output = invoke(query="python")
    assert output["error_code"] == code and len(calls) == 1
    assert "test-secret" not in json.dumps(output)


@pytest.mark.parametrize("response,code", [
    (Response(raw=b"invalid"), "invalid_response"),
    (Response({"error": {"message": "test-secret"}}), "provider_error"),
    (Response({}), "invalid_response"),
    (Response(raw=b"x" * 1_048_577), "response_too_large"),
])
def test_invalid_provider_responses(monkeypatch, response, code):
    monkeypatch.setattr(search.requests, "post", lambda *a, **kw: response)
    assert invoke(query="python")["error_code"] == code
    assert response.closed


def test_config_rotation_and_missing_key(monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY")
    assert invoke(query="python")["error_code"] == "missing_key"
    keys = []
    def post(*a, **kw):
        keys.append(kw["headers"]["Authorization"])
        return Response({"search_result": []})
    monkeypatch.setattr(search.requests, "post", post)
    for key in ("first-key", "second-key"):
        monkeypatch.setenv("ZHIPU_API_KEY", key)
        assert invoke(query="python")["ok"]
    assert keys == ["Bearer first-key", "Bearer second-key"]
