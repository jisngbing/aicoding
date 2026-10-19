"""Verify MongoDB memory, tool loop and checkpoint recovery without a model API."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deepagents import create_deep_agent
from deepagents.backends import StateBackend, StoreBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agent.core.checkpoint_history import visible_checkpoint_messages
from agent.core.graph import get_checkpointer, get_langgraph_store
from agent.core.persistence import get_mongo_client


class ToolCapableFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def main() -> None:
    get_mongo_client().admin.command("ping")
    print("MongoDB authentication: PASS", flush=True)
    saver, store = get_checkpointer(), get_langgraph_store()
    test_id = "mongodb-check-" + uuid.uuid4().hex
    namespace = ("aicoding-diagnostics", test_id)
    backend = StoreBackend(store=store, namespace=lambda _: namespace)
    config = {"configurable": {"thread_id": test_id}}
    try:
        result = backend.write("/memory.md", "MongoDB 记忆验证")
        assert not result.error, result.error
        assert "MongoDB 记忆验证" in backend.read("/memory.md").file_data["content"]
        print("StoreBackend memory write/read: PASS", flush=True)

        @tool
        def diagnostic_tool() -> str:
            """Return a deterministic diagnostic result."""
            return "diagnostic-ok"

        model = ToolCapableFakeModel(responses=[
            AIMessage(content="", tool_calls=[{
                "id": "diagnostic-call", "name": "diagnostic_tool", "args": {},
            }]),
            AIMessage(content="技术方案：MongoDB 验证完成。是否确认实施该方案？"),
        ])
        graph = create_deep_agent(model=model, tools=[diagnostic_tool], backend=StateBackend(),
                                  checkpointer=saver, store=store)
        output = graph.invoke({"messages": [{"role": "user", "content": "验证记忆"}]}, config)
        assert any(m.type == "tool" and m.content == "diagnostic-ok" for m in output["messages"])
        history = visible_checkpoint_messages(test_id)
        assert any("技术方案" in m["content"] for m in history)
        from agent.core.runtime import _latest_confirmable_plan_from_checkpoint
        assert _latest_confirmable_plan_from_checkpoint(test_id) is not None
        print("Agent tool loop, delta history and plan confirmation lookup: PASS", flush=True)
    finally:
        saver.delete_thread(test_id)
        store.delete(namespace, "/memory.md")


if __name__ == "__main__":
    main()
