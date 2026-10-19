"""Regression checks for directory permissions and streaming completion."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepagents.middleware.filesystem import _check_fs_permission
from agent.server import _agent_filesystem_permissions, _general_purpose_subagent, _code_reviewer_subagent
from agent.core import streaming_runtime as runtime


class Stream(list):
    def __init__(self, events, messages):
        super().__init__(events)
        self.output = {"messages": messages}


class Agent:
    def __init__(self, stream):
        self.stream = stream

    def stream_events(self, *args, **kwargs):
        return self.stream


def main():
    rules = _agent_filesystem_permissions()
    for directory in ("/projects", "/skills", "/policies", "/reviews", "/runtimes", "/logs", "/tmp", "/memories"):
        assert _check_fs_permission(rules, "read", directory) == "allow", directory
    assert _check_fs_permission(rules, "read", "/.secrets/token") == "deny"
    assert _check_fs_permission(rules, "write", "/skills/a") == "deny"
    for subagent in (_general_purpose_subagent(None), _code_reviewer_subagent(None)):
        assert _check_fs_permission(subagent["permissions"], "read", "/projects") == "allow"
        assert _check_fs_permission(subagent["permissions"], "write", "/projects/a.py") == "deny"
    print("Directory roots allowed; protected paths and reviewer writes denied: passed")

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    def run(stream):
        events = []
        with patch.object(runtime, "record_event"), patch.object(runtime, "_finalize_empty_answer", return_value=""):
            runtime.run_agent_with_event_stream(agent=Agent(stream), thread_id="test", run_id="run", content="test",
                event_sink=lambda kind, data: events.append((kind, data)))
        return events

    final = [HumanMessage(content="test"), AIMessage(content="DONE")]
    tool_start = {"method": "tools", "params": {"data": {"event": "tool-started", "tool_name": "ls"}}}
    delta = {"method": "messages", "params": {"data": [{"event": "content-block-delta", "delta": {"type": "text-delta", "text": "DONE"}}, {}]}}
    events = run(Stream([tool_start, delta], final))
    assert any(kind == "status" and "ls" in data["message"] for kind, data in events)
    assert sum(kind == "text_delta" for kind, _ in events) == 1
    events = run(Stream([], final))
    assert any(kind == "text_delta" and data["content"] == "DONE" for kind, data in events)
    empty = [AIMessage(content="Old plan"), HumanMessage(content="new task"),
             ToolMessage(content="permission denied", tool_call_id="check"), AIMessage(content="")]
    try:
        run(Stream([], empty))
    except RuntimeError as error:
        assert "permission denied" in str(error)
    else:
        raise AssertionError("An empty current answer must fail instead of reusing the old plan")
    print("Tool progress, final-answer fallback, and empty-answer failure: passed")

    captured = []
    with patch.object(runtime, "record_event"), patch.object(runtime, "_finalize_empty_answer", return_value="目录为空，尚未修改文件。") as finalize:
        result = runtime.run_agent_with_event_stream(agent=Agent(Stream([], empty)), thread_id="test", run_id="run",
            content="test", event_sink=lambda kind, data: captured.append((kind, data)))
        assert finalize.call_count == 1
        assert result["messages"][-1]["content"] == "目录为空，尚未修改文件。"
        assert any(kind == "text_delta" for kind, _ in captured)
    print("Empty tool-only responses get one bounded finalization: passed")
    try:
        run(Stream([], [HumanMessage(content="test"), AIMessage(content="<tool_call><function=execute>git clone</function></tool_call>")]))
    except RuntimeError as error:
        assert "没有执行对应命令" in str(error)
    else:
        raise AssertionError("Plain XML must not mark a task complete")
    print("Plain XML tool instructions cannot mark a task complete: passed")


if __name__ == "__main__":
    main()
