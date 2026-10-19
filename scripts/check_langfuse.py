"""Send a model/tool diagnostic trace without calling a paid model API."""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from agent.core.observability import get_langfuse_client, shutdown_tracing, trace_config


class DiagnosticModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@tool
def diagnostic_echo(text: str) -> str:
    """Return diagnostic text without changing local files or calling external APIs."""
    return text


def main() -> None:
    client = get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse tracing is disabled or the three LANGFUSE settings are missing")
    try:
        if not client.auth_check():
            raise RuntimeError("Langfuse authentication failed")
        print("Langfuse authentication: PASS", flush=True)
        session_id = "aicoding-sdk-check-" + uuid.uuid4().hex
        config = trace_config(operation="aicoding.sdk-check", thread_id=session_id,
                              run_id=uuid.uuid4().hex, task_kind="diagnostic")
        model = DiagnosticModel(responses=[
            AIMessage(content="", tool_calls=[{"id": "diagnostic-call", "name": "diagnostic_echo", "args": {"text": "Langfuse SDK connected"}}]),
            AIMessage(content="Langfuse SDK verification complete"),
        ])
        graph = create_deep_agent(model=model, tools=[diagnostic_echo]).with_config(config)
        graph.invoke({"messages": [{"role": "user", "content": "Verify Langfuse model and tool tracing"}]})
        client.flush()
        trace_id = config["callbacks"][-1].last_trace_id
        assert trace_id, "No trace was created"
        with httpx.Client(timeout=10, trust_env=False, auth=(
            os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]
        )) as http:
            for attempt in range(15):
                response = http.get(os.environ["LANGFUSE_BASE_URL"].rstrip("/") + "/api/public/v2/observations",
                                    params={"traceId": trace_id, "limit": 100})
                response.raise_for_status()
                types = {row["type"] for row in response.json()["data"]}
                if {"GENERATION", "TOOL"} <= types:
                    print("Model and tool observations persisted: PASS", flush=True)
                    print("Session: " + session_id)
                    print("Trace: " + trace_id)
                    return
                time.sleep(2)
        raise RuntimeError("Trace export did not become queryable within 30 seconds")
    finally:
        shutdown_tracing()


if __name__ == "__main__":
    main()
