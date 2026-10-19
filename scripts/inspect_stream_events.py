"""Inspect a short, isolated model stream to diagnose protocol compatibility."""
from pathlib import Path
import json
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.server import get_agent

runner = get_agent({"configurable": {
    "thread_id": "stream-shape-" + uuid4().hex,
    "task_kind": "qa",
    "__is_for_execution__": True,
}})
stream = runner.stream_events({"messages": [{"role": "user", "content": "只回复 STREAM_OK，不要调用工具。"}]}, version="v3")
shown = 0
for event in stream:
    if event.get("method") == "messages" and shown < 6:
        print(json.dumps(event, ensure_ascii=False, default=str)[:1800])
        shown += 1
print("STREAM_COMPLETED")
