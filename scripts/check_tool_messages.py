"""Check that empty content-block IDs cannot discard valid tool results."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import AIMessage, ToolMessage
from agent.core.middleware.message_sanitize import sanitize_messages_for_model, _is_valid_tool_call, repair_model_tool_call_ids
from langchain.agents.middleware.types import ModelResponse

valid = {"name": "ls", "id": "call-valid", "args": {"path": "/projects"}, "type": "tool_call"}
assistant = AIMessage(content=[{"type": "tool_call", "id": "", "name": "ls", "args": {"path": "/projects"}}], tool_calls=[valid])
tool = ToolMessage(content="No files found", tool_call_id="call-valid")
result = sanitize_messages_for_model([assistant, tool])
assert len(result) == 2
assert result[0].tool_calls == [valid]
assert isinstance(result[1], ToolMessage) and result[1].content == "No files found"
assert not _is_valid_tool_call({"name": "ls", "id": "", "args": {}})
assert not _is_valid_tool_call({"name": "ls", "id": "   ", "args": {}})
assert not _is_valid_tool_call({"name": "", "id": "valid", "args": {}})
assert sanitize_messages_for_model(result) == result
missing_id = AIMessage(content="", tool_calls=[{"name": "execute", "id": "", "args": {"command": "git --version"}}])
repaired = repair_model_tool_call_ids(ModelResponse(result=[missing_id])).result[0]
assert repaired.tool_calls[0]["id"].startswith("call_")
assert repair_model_tool_call_ids(repaired).tool_calls == repaired.tool_calls
paired = sanitize_messages_for_model([repaired, ToolMessage(content="git version", tool_call_id=repaired.tool_calls[0]["id"])])
assert len(paired) == 2
assert sanitize_messages_for_model([ToolMessage(content="stale result", tool_call_id="")]) == []
print("Valid tool call/result preserved; empty IDs rejected; repeated cleaning stable: passed")
