"""Exercise the rare empty-answer recovery with a real model and an in-memory checkpoint."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from agent import server
from agent.core import graph, streaming_runtime

config = {"configurable": {"thread_id": "finalization-check", "task_kind": "qa", "__is_for_execution__": True}}
seed = [HumanMessage(content="读取空目录，不修改文件。"),
        AIMessage(content="", tool_calls=[{"name": "ls", "id": "check-call", "args": {"path": "/projects"}}]),
        ToolMessage(content="No files found", tool_call_id="check-call"), AIMessage(content="")]
with patch.object(server, "get_langgraph_store", return_value=InMemoryStore()), patch.object(graph, "get_checkpointer", return_value=InMemorySaver()):
    agent = server.get_agent(config)
    compiled = getattr(agent, "bound", agent)
    compiled.update_state(config, {"messages": seed}, as_node="model")
    events = []
    with patch.object(streaming_runtime, "record_event"):
        answer = streaming_runtime._finalize_empty_answer(agent=agent,
            messages=[{"type": item.type, "content": item.content} for item in seed],
            content="请说明目录检查结果，不修改文件。", thread_id="finalization-check", run_id="test",
            task_kind="qa", event_sink=lambda kind, data: events.append((kind, data)))
    assert answer and any(kind == "text_delta" for kind, _ in events)
    assert compiled.get_state(config).values["messages"][-1].content == answer
    print("Tool-free final answer streaming and checkpoint persistence: passed")
