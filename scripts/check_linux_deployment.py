"""Check the authenticated public UI, API, model SSE and history recovery."""
from __future__ import annotations

import json
from pathlib import Path

import requests


def main() -> None:
    path = Path(__file__).resolve().parents[1] / "data/linux-deployment/access.txt"
    access = dict(line.split(": ", 1) for line in path.read_text(encoding="utf-8").splitlines() if ": " in line)
    base = access["URL"].rstrip("/")
    assert requests.get(base, timeout=15).status_code == 401
    session = requests.Session()
    session.auth = (access["Username"], access["Password"])
    page = session.get(base, timeout=15)
    page.raise_for_status()
    page.encoding = "utf-8"
    assert "AI Coding" in page.text
    for endpoint in ("/health", "/dashboard/api/me", "/dashboard/api/options", "/dashboard/api/threads"):
        response = session.get(base + endpoint, timeout=30)
        response.raise_for_status()
        response.json()
    dashboard_options = session.get(base + "/dashboard/api/options", timeout=30).json()
    print("Public authentication, UI and API: PASS", flush=True)
    thread_id = None
    expected = "Linux 部署验证通过"
    texts = {}
    event = ""
    done = False
    try:
        with session.post(base + "/dashboard/api/threads/stream-message", json={
            "repo": dashboard_options.get("default_repo_url") or None,
            "content": f"这是一条连通性问答，请只输出‘{expected}’，不要调用工具、创建方案或修改文件。",
        }, stream=True, timeout=(15, 180)) as response:
            response.raise_for_status()
            response.encoding = "utf-8"
            for line in response.iter_lines(decode_unicode=True):
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    if event == "thread_snapshot":
                        thread_id = data["id"]
                    elif event == "text_delta":
                        message_id = data.get("message_id", "assistant")
                        if data.get("mode") == "replace":
                            texts[message_id] = data.get("content", "")
                        else:
                            texts[message_id] = texts.get(message_id, "") + data.get("content", "")
                    elif event == "error":
                        raise RuntimeError("Agent SSE returned an error; inspect backend logs")
                    elif event == "done":
                        done = True
        assert done and any(expected in text for text in texts.values()), "Missing model response or done event"
        history = session.get(base + f"/dashboard/api/threads/{thread_id}", timeout=30)
        history.raise_for_status()
        assert expected in json.dumps(history.json(), ensure_ascii=False)
        print("Qwen SSE, done event and persisted history: PASS", flush=True)
    finally:
        if thread_id:
            session.delete(base + f"/dashboard/api/threads/{thread_id}", timeout=30).raise_for_status()
            print("Temporary smoke-test conversation cleaned up", flush=True)


if __name__ == "__main__":
    main()
