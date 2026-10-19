from unittest.mock import Mock

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent.core import observability


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr("agent.env_utils.load_environment", lambda: None)
    monkeypatch.setattr(observability, "load_environment", lambda: None)
    monkeypatch.setattr(observability, "get_display_secrets", lambda: ("private-test-key",))
    monkeypatch.setattr(observability, "_client", None)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test-aicoding")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "private-test-key")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")


def test_disabled_or_incomplete_config_does_not_initialize(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(observability, "Langfuse", factory)
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "false")
    config = {"configurable": {"thread_id": "existing"}}
    assert observability.trace_config(config, operation="test") == config
    monkeypatch.setenv("LANGFUSE_TRACING_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")
    assert observability.trace_config(config, operation="test") == config
    factory.assert_not_called()


def test_initialization_error_preserves_execution_config(monkeypatch, caplog):
    monkeypatch.setattr(observability, "Langfuse", Mock(side_effect=RuntimeError("private-test-key")))
    config = {"configurable": {"thread_id": "thread"}, "tags": ["existing"]}
    assert observability.trace_config(config, operation="test") == config
    assert "initialization failed" in caplog.text
    assert "private-test-key" not in caplog.text


def test_structured_mask_preserves_usage_and_input():
    source = {"api_key": "private-test-key", "usage": {"input_tokens": 12},
              "messages": [{"content": "Bearer private-test-key"}]}
    masked = observability._mask_data(source, ("private-test-key",))
    assert masked["api_key"] == "***"
    assert masked["usage"]["input_tokens"] == 12
    assert "private-test-key" not in str(masked)
    assert source["api_key"] == "private-test-key"


def test_actual_stream_records_model_tool_session_and_run(monkeypatch):
    from deepagents import create_deep_agent
    from agent.core import streaming_runtime

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    client = Langfuse(public_key="pk-lf-test-aicoding", secret_key="private-test-key",
                      base_url="http://127.0.0.1:1", tracer_provider=provider, span_exporter=exporter,
                      mask_otel_spans=lambda *, params: observability._mask_export(params=params, secrets=("private-test-key",)),
                      mask=lambda data: observability._mask_data(data, ("private-test-key",)))
    monkeypatch.setattr(observability, "_client", client)
    monkeypatch.setattr(streaming_runtime, "record_event", Mock())

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def echo(text: str) -> str:
        """Echo the diagnostic text."""
        return text

    previous = BaseCallbackHandler()
    original = {"callbacks": [previous], "configurable": {"thread_id": "trace-test"},
                "metadata": {"existing": "preserved"}}
    config = observability.trace_config(original, operation="aicoding.agent", thread_id="trace-test", task_kind="qa")
    assert original["callbacks"] == [previous]
    assert config["callbacks"][0] is previous
    assert config["metadata"]["existing"] == "preserved"
    other = observability.trace_config(operation="test", thread_id="other")
    assert other["callbacks"][0] is not config["callbacks"][-1]
    model = Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "echo-call", "name": "echo", "args": {"text": "private-test-key"}}]),
        AIMessage(content="diagnostic complete"),
    ])
    try:
        graph = create_deep_agent(model=model, tools=[echo]).with_config(config)
        result = streaming_runtime.run_agent_with_event_stream(
            agent=graph, thread_id="trace-test", run_id="run-test", content="Run echo", task_kind="qa"
        )
        assert result["messages"][-1]["content"] == "diagnostic complete"
        client.flush()
        spans = exporter.get_finished_spans()
        assert spans
        assert {s.attributes.get("langfuse.observation.type") for s in spans} >= {"generation", "tool"}
        assert any(s.attributes.get("session.id") == "trace-test" for s in spans)
        assert any("run-test" in str(s.attributes) for s in spans)
        assert "private-test-key" not in str([dict(s.attributes) for s in spans])
    finally:
        client.shutdown()
        provider.shutdown()
