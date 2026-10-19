import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from agent.core.tool_display import ToolDisplayTracker, display_text, tool_result, tracked_tool_events


def test_display_redacts_nested_credentials_without_mutating_input(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-private-example-value")
    source = {"headers": {"Authorization": "Bearer abc123"}, "password": "secret-value",
              "text": "sk-private-example-value https://user:pwd@example.com?access_token=abc123"}
    text = display_text(source)
    for secret in ("abc123", "secret-value", "sk-private-example-value", "user:pwd"):
        assert secret not in text
    assert source["password"] == "secret-value"
    assert json.loads(text)["password"] == "***"
    assert "已截断" in display_text("x" * 8000)


@pytest.mark.parametrize("output", [
    ToolMessage(content='{"ok":false,"error":"missing"}', tool_call_id="c1", status="error"),
    Command(update={"messages": [ToolMessage(content="missing", tool_call_id="c1", status="error")]}),
    {"exit_code": 1, "output": "command failed"},
    ToolMessage(content="exit_code=1\npytest failed", tool_call_id="c1"),
    "exit_code=124\ncommand timed out",
])
def test_handled_tool_failures_are_not_displayed_as_success(output):
    assert tool_result(output)[0] == "error"


def test_parallel_calls_keep_identity_and_finish_status_without_names_on_end():
    emitted, recorded = [], []
    tracker = ToolDisplayTracker("thread", "run", lambda *a, **kw: recorded.append(kw),
                                 lambda event, data: emitted.append((event, data)))
    tracker.observe({"event": "tool-started", "tool_call_id": "a", "tool_name": "read_file", "input": {"path": "a.py"}})
    tracker.observe({"event": "tool-started", "tool_call_id": "b", "tool_name": "read_file", "input": {"path": "b.py"}})
    tracker.observe({"event": "tool-finished", "tool_call_id": "b", "output": "file b"})
    tracker.observe({"event": "tool-error", "tool_call_id": "a", "message": "not found"})
    tracker.finish()
    assert len(emitted) == 4
    assert emitted[0][1]["status"] == "in_progress"  # Earlier payload is not mutated.
    assert emitted[2][1]["message_id"] == emitted[1][1]["message_id"]
    assert tracker.calls["a"]["status"] == "error"
    assert tracker.calls["b"]["status"] == "completed"
    assert "duration_ms" in emitted[-1][1]
    assert json.loads(recorded[-1]["detail"])["output"] == "not found"


def test_stream_failure_settles_unfinished_cards_and_preserves_exception():
    tracker = ToolDisplayTracker("thread", "run", lambda *a, **kw: None, None)
    def stream():
        yield {"method": "tools", "params": {"data": {"event": "tool-started", "tool_call_id": "a", "tool_name": "execute"}}}
        raise RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="connection lost"):
        list(tracked_tool_events(stream(), tracker))
    assert tracker.calls["a"]["status"] == "interrupted"


def test_todos_keep_their_existing_dedicated_display():
    tracker = ToolDisplayTracker("thread", "run", lambda *a, **kw: None, None)
    tracker.observe({"event": "tool-started", "tool_call_id": "a", "tool_name": "write_todos"})
    tracker.observe({"event": "tool-finished", "tool_call_id": "a", "output": "updated"})
    assert not tracker.calls


def test_tuple_events_preserve_execution_result_and_failed_exit_code():
    emitted = []
    tracker = ToolDisplayTracker("thread", "run", lambda *a, **kw: None,
                                 lambda name, data: emitted.append((name, data)))
    start = {"event": "tool-started", "tool_call_id": "a", "tool_name": "execute", "input": {"command": "pytest"}}
    end = {"event": "tool-finished", "tool_call_id": "a", "output": "exit_code=1\ntests failed"}
    events = [{"method": "tools", "params": {"data": (payload, {"node": "tools"})}}
              for payload in (start, end)]
    assert list(tracked_tool_events(events, tracker)) == events
    assert [data["status"] for _, data in emitted] == ["in_progress", "error"]
    assert emitted[-1][1]["output"] == "exit_code=1\ntests failed"


def test_successful_execute_text_stays_successful():
    assert tool_result("exit_code=0\n42 passed")[0] == "completed"


def test_history_loads_redaction_configuration_once(monkeypatch):
    from agent.core.checkpoint_history import visible_checkpoint_messages
    monkeypatch.setenv("GITEE_TOKEN", "short")
    messages = [AIMessage(content="", tool_calls=[{
        "id": "a", "name": "echo", "args": {"items": ["short", "short", "short"]},
    }]), ToolMessage(content="short", tool_call_id="a")]
    with patch("agent.core.checkpoint_history._delta_messages_from_checkpoint", return_value=messages), \
         patch("agent.core.tool_display.load_environment") as load:
        history = visible_checkpoint_messages("thread", include_tools=True)
    load.assert_called_once()
    assert "short" not in json.dumps(history)


def test_checkpoint_restores_tool_results_and_keeps_normal_history_contract():
    from agent.core.checkpoint_history import visible_checkpoint_messages
    messages = [
        AIMessage(content="", tool_calls=[{"id": "a", "name": "read_file", "args": {"path": "a.py"}}]),
        ToolMessage(content="file body", tool_call_id="a"),
        AIMessage(content="", tool_calls=[{"id": "a", "name": "read_file", "args": {"path": "a.py"}}]),
    ]
    with patch("agent.core.checkpoint_history._delta_messages_from_checkpoint", return_value=messages):
        assert visible_checkpoint_messages("thread") == []
        history = visible_checkpoint_messages("thread", include_tools=True)
    assert len(history) == 1
    assert history[0]["tool"]["status"] == "completed"
    assert history[0]["tool"]["output"] == "file body"


def test_real_langchain_v3_tool_events_reach_frontend_protocol():
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.tools import tool
    from agent.core.streaming_runtime import _consume_raw_event_stream

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    @tool
    def echo(text: str) -> str:
        """Return the supplied text."""
        return text

    model = Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "echo-call", "name": "echo", "args": {"text": "工具结果"}}]),
        AIMessage(content="已处理完成"),
    ])
    agent = create_agent(model=model, tools=[echo])
    events = []
    with patch("agent.core.streaming_runtime.record_event"):
        _consume_raw_event_stream(
            stream=agent.stream_events({"messages": [{"role": "user", "content": "测试工具"}]}, version="v3"),
            thread_id="test", run_id="run", task_kind="qa",
            event_sink=lambda name, data: events.append((name, data)),
        )
    calls = [data for name, data in events if name == "tool_delta"]
    assert [call["status"] for call in calls] == ["in_progress", "completed"]
    assert calls[-1]["output"] == "工具结果"
    assert calls[0]["message_id"] == calls[-1]["message_id"]
