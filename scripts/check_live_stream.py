"""Verify the same SSE route used by the frontend with a read-only tool request."""
import json
import httpx
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.env_utils import get_env

events = []
current_event = ""
with httpx.Client(timeout=90) as client:
    with client.stream("POST", "http://127.0.0.1:3000/dashboard/api/threads/stream-message", json={
        "repo": get_env("DEFAULT_REPO_URL") or None,
        "content": "测试：请调用 ls 工具读取 /projects 目录，随后只回复目录检查完成。禁止修改文件、调用搜索或访问远程仓库。",
    }) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                events.append((current_event, data))
                if current_event in {"status", "error", "done"}:
                    print(current_event, data.get("message", ""))
visible = [data for kind, data in events if kind == "text_delta"
           and "assistant-startup" not in str(data.get("message_id"))]
errors = [data for kind, data in events if kind == "error"]
assert any(kind == "done" for kind, _ in events), "SSE did not complete"
assert not errors, errors
assert visible, "No model answer was streamed"
assert any("ls" in data.get("message", "") for kind, data in events if kind == "status"), "Tool progress missing"
print("Frontend proxy, directory tool, model text, and SSE completion: passed")
