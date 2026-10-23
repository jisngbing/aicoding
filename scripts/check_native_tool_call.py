"""Inspect a native Qwen tool call using a harmless command."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.core.models.model import make_main_model
from agent.backends.local_shell import LocalShellBackend
from agent.server import _controlled_execute_tool

execute = _controlled_execute_tool(LocalShellBackend())
response = make_main_model().bind_tools([execute], tool_choice={"type": "function", "function": {"name": "execute"}}).invoke(
    "调用 execute 工具，command 参数必须严格为 git --version。不要返回 XML 或命令示例。")
print(json.dumps({"tool_calls": response.tool_calls, "text": str(response.content)[:300]}, ensure_ascii=False))
assert response.tool_calls, "The model did not return a native tool call"
call = response.tool_calls[0]
assert call["name"] == "execute" and call["args"]["command"] == "git --version", call
print("Native tool call produced; nonempty ID:", bool(call.get("id")))
