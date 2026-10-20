"""Build actual DeepAgents graphs without model requests or remote Git operations."""
from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from deepagents.backends import CompositeBackend

from agent import server
from agent.backends.local_shell import LocalShellBackend
from agent.backends.file_only import FileOnlyBackend
from agent.core import graph


def main() -> None:
    with TemporaryDirectory(prefix="aicoding-backend-check-") as temporary:
        backend = LocalShellBackend(workspace=temporary)
        store = InMemoryStore()
        checkpoint = InMemorySaver()
        with (
            patch.object(server, "ensure_backend_for_thread", return_value=backend),
            patch.object(server, "get_langgraph_store", return_value=store),
            patch.object(graph, "get_checkpointer", return_value=checkpoint),
            patch.object(server, "create_deep_agent", wraps=server.create_deep_agent) as create_agent,
        ):
            for repo_url in (None, "https://gitee.com/bing-jiang123456/aicoding"):
                configuration = {"configurable": {
                    "thread_id": "backend-compatibility-check",
                    "task_kind": "qa",
                    "__is_for_execution__": True,
                    "repo_url": repo_url,
                }}
                runnable = server.get_agent(configuration)
                initialized = create_agent.call_args.kwargs["backend"]
                assert not callable(initialized), "Backend factories must not be passed"
                assert isinstance(initialized, FileOnlyBackend)
                assert not hasattr(initialized, "execute")
                if repo_url:
                    assert isinstance(initialized.backend, CompositeBackend)
                    memory_path = create_agent.call_args.kwargs["memory"][0]
                    result = initialized.read(memory_path)
                    assert not result.error and result.file_data, result
                else:
                    assert initialized.backend is backend
                arguments = create_agent.call_args.kwargs
                assert arguments["permissions"]
                for subagent in arguments["subagents"]:
                    assert subagent["permissions"]
                    assert all(tool.name != "execute" for tool in subagent["tools"])
                execute_tool = next(tool for tool in arguments["tools"] if tool.name == "execute")
                assert "exit_code=126" in execute_tool.invoke({"command": "shutdown /s"})
                assert "exit_code=0" in execute_tool.invoke({"command": "git --version"})
                assert runnable.get_graph().nodes
                print(f"Actual DeepAgent graph ({'repository memory' if repo_url else 'local sandbox'}): passed")


if __name__ == "__main__":
    main()
