"""工具调用展示协议；参数和结果在进入 SSE、历史记录前统一脱敏。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from agent.env_utils import load_environment

_SECRET_KEY = re.compile(r"token|password|passwd|secret|api[_-]?key|authorization|cookie|credential", re.I)
_SECRET_VALUE = re.compile(
    r"(\b(?:[\w-]*(?:token|password|passwd|secret|api[_-]?key)|authorization|cookie)"
    r"\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&,;}]+)", re.I
)


def get_display_secrets() -> tuple[str, ...]:
    """Snapshot credentials once per display operation or history request."""
    def current() -> set[str]:
        return {secret for name, secret in os.environ.items()
                if _SECRET_KEY.search(name) and secret
                and (len(secret) >= 6 or name in {"GITEE_TOKEN", "SCM_GITEE_TOKEN"})}

    # Cover both inherited values and values reloaded from the project's .env.
    secrets = current()
    load_environment()
    secrets.update(current())
    return tuple(sorted(secrets, key=len, reverse=True))


def display_text(value: Any, limit: int = 6000, *, secrets: tuple[str, ...] | None = None) -> str:
    """格式化展示副本，避免改动真实工具参数或消息内容。"""

    if secrets is None:
        secrets = get_display_secrets()

    def scrub(text: str) -> str:
        for secret in secrets:
            text = text.replace(secret, "***")
        text = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer ***", text)
        text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1***@", text)
        return _SECRET_VALUE.sub(lambda match: match.group(1) + "***", text)

    def redact(item: Any, depth: int = 0) -> Any:
        if depth > 12:
            return "…"
        if isinstance(item, dict):
            return {str(k): "***" if _SECRET_KEY.search(str(k)) else redact(v, depth + 1)
                    for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [redact(v, depth + 1) for v in item[:100]]
        if isinstance(item, str):
            try:
                decoded = json.loads(item)
            except (ValueError, TypeError):
                return scrub(item)
            return redact(decoded, depth + 1) if isinstance(decoded, (dict, list)) else scrub(item)
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return scrub(str(item))

    value = redact(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str, indent=2)
    return text if len(text) <= limit else text[:limit] + "\n…（内容已截断）"


def tool_result(output: Any, *, secrets: tuple[str, ...] | None = None) -> tuple[str, str]:
    """识别异常 ToolMessage 和 Command 中的工具结果，避免把恢复后的错误标成成功。"""
    update = getattr(output, "update", None)
    if isinstance(update, dict):
        messages = update.get("messages") or []
        tool_messages = [m for m in messages if getattr(m, "type", None) == "tool"]
        if tool_messages:
            return tool_result(tool_messages[-1], secrets=secrets)
    status = getattr(output, "status", None)
    content = getattr(output, "content", output)
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            parsed = None
    else:
        parsed = content
    # The project's execute tool returns text rather than a JSON dictionary.
    exit_match = re.match(r"\s*exit_code\s*=\s*(-?\d+)\b", content) if isinstance(content, str) else None
    failed = status == "error" or (exit_match is not None and int(exit_match.group(1)) != 0) or (
        isinstance(parsed, dict) and (
            parsed.get("ok") is False or parsed.get("status") == "error"
            or parsed.get("exit_code") not in (None, 0, "0")
        )
    )
    return ("error" if failed else "completed"), display_text(content, secrets=secrets)


class ToolDisplayTracker:
    """以 tool_call_id 合并开始和结束事件，同一调用始终对应一张卡片。"""

    def __init__(self, thread_id: str, run_id: str, record: Callable, sink: Callable | None):
        self.thread_id = thread_id
        self.run_id = run_id
        self.record = record
        self.sink = sink
        self.calls: dict[str, dict[str, Any]] = {}
        self.started: dict[str, float] = {}

    def observe(self, event: dict[str, Any]) -> None:
        call_id = str(event.get("tool_call_id") or "")
        kind = event.get("event")
        if not call_id or kind not in {"tool-started", "tool-finished", "tool-error"}:
            return
        if kind == "tool-started" and event.get("tool_name") == "write_todos":
            return  # Todo 已有专用卡片。
        if kind != "tool-started" and call_id not in self.calls:
            return
        if kind == "tool-started":
            if call_id in self.calls:
                return
            self.started[call_id] = monotonic()
            self.calls[call_id] = {
                "message_id": f"{self.thread_id}-tool-{self.run_id}-{call_id}",
                "run_id": self.run_id,
                "tool_call_id": call_id,
                "tool_name": str(event.get("tool_name") or "unknown_tool"),
                "input": display_text(event.get("input", {})),
                "output": "",
                "status": "in_progress",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        else:
            call = self.calls[call_id]
            if kind == "tool-error":
                call.update(status="error", output=display_text(event.get("message", "工具执行失败")))
            else:
                status, output = tool_result(event.get("output"))
                call.update(status=status, output=output)
            call["duration_ms"] = round((monotonic() - self.started[call_id]) * 1000)
        self._emit(call_id)

    def _emit(self, call_id: str) -> None:
        call = dict(self.calls[call_id])
        self.record(self.thread_id, f"stream:{self.run_id}:tool:{call_id}",
                    f"调用工具：{call['tool_name']}", kind="tool", status=call["status"],
                    detail=json.dumps(call, ensure_ascii=False))
        if self.sink is not None:
            self.sink("tool_delta", call)

    def finish(self) -> None:
        for call_id, call in self.calls.items():
            if call["status"] == "in_progress":
                call.update(status="interrupted", output="执行流已结束，未收到工具执行结果。")
                call["duration_ms"] = round((monotonic() - self.started[call_id]) * 1000)
                self._emit(call_id)


def tracked_tool_events(stream: Any, tracker: ToolDisplayTracker):
    """即使运行上限或流异常打断，也结束尚未收到结果的工具卡片。"""
    try:
        for event in stream:
            if isinstance(event, dict) and event.get("method") == "tools":
                data = (event.get("params") or {}).get("data")
                # V3 may wrap a payload as (payload, metadata).
                if isinstance(data, tuple):
                    payloads = [data[0]] if data else []
                else:
                    payloads = data if isinstance(data, list) else [data]
                for payload in payloads:
                    if isinstance(payload, dict):
                        tracker.observe(payload)
            yield event
    finally:
        tracker.finish()
