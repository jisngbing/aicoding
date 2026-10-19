"""Exercise native V3 tool execution against the user's repository; never push."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_core.messages import ToolMessage
from agent.core.model import make_main_model
from agent.core.middleware.message_sanitize import MessageSanitizeMiddleware
from agent.backends.local_shell import LocalShellBackend
from agent.server import _controlled_execute_tool

backend = LocalShellBackend()
destination = backend.projects_dir / "test"
expected = "git -C test status --short" if (destination / ".git").exists() else "git clone https://gitee.com/bing-jiang123456/test.git test"
if destination.exists() and not (destination / ".git").exists():
    raise RuntimeError("test directory already exists but is not a Git repository; it was not changed")
controlled = _controlled_execute_tool(backend)
executed = []
results = []

@tool
def execute(command: str, timeout: int = 60) -> str:
    """Execute the single permitted Git test command, and return its real result."""
    if command != expected:
        return "测试拒绝：command 必须严格等于 " + expected
    if executed:
        return results[0]
    executed.append(command)
    result = controlled.invoke({"command": command, "timeout": min(timeout, 60)})
    results.append(result)
    return result

agent = create_agent(model=make_main_model(), tools=[execute], middleware=[MessageSanitizeMiddleware()],
    system_prompt="只完成指定的 Git 本地测试。使用原生结构化工具调用，不要输出 XML。调用工具一次后根据真实结果输出一句中文结论，不要继续调用工具。")
stream = agent.stream_events({"messages": [{"role": "user", "content": "请调用 execute，command 必须严格等于：" + expected}]}, version="v3", config={"recursion_limit": 12})
for event in stream:
    pass
messages = stream.output["messages"]
assert executed == [expected], "No real tool execution occurred"
assert results and "exit_code=0" in results[0], results
assert (destination / ".git").exists(), "Repository was not cloned"
assert all(message.tool_call_id for message in messages if isinstance(message, ToolMessage))
print("Native V3 tool execution: passed")
print("Git command exit code: 0")
print("Local repository:", destination)
print("Repository verification:", backend.execute("git -C test rev-parse --is-inside-work-tree").output)
