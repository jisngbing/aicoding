"""Langfuse tracing shared by Agent, intent classification and answer generation."""
from __future__ import annotations

import json
import logging
import os
import re
from threading import RLock
from typing import Any

from langchain_core.runnables.config import merge_configs
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

from agent.core.tool_display import get_display_secrets
from agent.core.answer_feedback import current_question
from agent.env_utils import load_environment

logger = logging.getLogger("agent.run.observability")
_client: Langfuse | None = None
_lock = RLock()
_SECRET_FIELD = re.compile(
    r"^token$|password|passwd|secret|api[_-]?key|authorization|cookie|credential|access[_-]?token", re.IGNORECASE
)
_SECRET_ASSIGNMENT = re.compile(
    r"(\b(?:[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token)|authorization|cookie)"
    r"\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s&,;}]+)", re.IGNORECASE
)


def _mask_data(value: Any, secrets: tuple[str, ...]) -> Any:
    """Redact a copy while preserving structured inputs and token usage counts."""
    if isinstance(value, dict):
        return {key: "***" if _SECRET_FIELD.search(str(key)) else _mask_data(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_mask_data(item, secrets) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_mask_data(parsed, secrets), ensure_ascii=False)
        for secret in secrets:
            value = value.replace(secret, "***")
        value = re.sub(r"(?i)\bBearer\s+[^\s\"'<>]+", "Bearer ***", value)
        value = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1***@", value)
        return _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + "***", value)
    return value


def _mask_export(*, params: MaskOtelSpansParams, secrets: tuple[str, ...]) -> MaskOtelSpansResult:
    """Also redact serialized messages/commands and exception attributes at export."""
    patches = {}
    for identifier, span in params.spans.items():
        changed = {}
        for key, value in span.attributes.items():
            masked = _mask_data(value, secrets)
            if masked != value:
                changed[key] = masked
        if changed:
            patches[identifier] = OtelSpanPatch(set_attributes=changed)
    return MaskOtelSpansResult(span_patches=patches)


def get_langfuse_client() -> Langfuse | None:
    """Initialize once per process; missing/disabled tracing never blocks a task."""
    global _client
    load_environment()
    if os.getenv("LANGFUSE_TRACING_ENABLED", "true").lower() in {"false", "0", "no"}:
        return None
    if not all(os.getenv(key, "").strip() for key in (
        "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"
    )):
        return None
    with _lock:
        if _client is None:
            try:
                secrets = get_display_secrets()
                _client = Langfuse(
                    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                    base_url=os.environ["LANGFUSE_BASE_URL"],
                    environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "development"),
                    timeout=5,
                    mask=lambda data: _mask_data(data, secrets),
                    mask_otel_spans=lambda *, params: _mask_export(params=params, secrets=secrets),
                )
                logger.info("Langfuse tracing initialized")
            except Exception as exc:  # noqa: BLE001 - observability must not stop Agent execution
                logger.warning("Langfuse initialization failed: %s", type(exc).__name__)
    return _client


def trace_config(
    config: dict[str, Any] | None = None,
    *,
    operation: str,
    thread_id: str | None = None,
    run_id: str | None = None,
    task_kind: str | None = None,
) -> dict[str, Any]:
    """Use a fresh handler per invocation, preserving existing runnable config."""
    client = get_langfuse_client()
    if client is None:
        return dict(config or {})
    try:
        question = current_question.get()
        handler = CallbackHandler(public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                                  trace_context={"trace_id": question["trace_id"]} if question and question.get("trace_id") else None)
        metadata: dict[str, Any] = {"langfuse_trace_name": operation}
        if thread_id:
            metadata["langfuse_session_id"] = thread_id
        if run_id:
            metadata["run_id"] = run_id
        if task_kind:
            metadata["task_kind"] = task_kind
        if question:
            metadata["question_id"] = question["question_id"]
            metadata["langfuse_session_id"] = question["thread_id"]
        return merge_configs(config, {"callbacks": [handler], "metadata": metadata, "tags": ["aicoding"]})
    except Exception as exc:  # noqa: BLE001 - keep the caller's runnable config usable
        logger.warning("Langfuse callback initialization failed: %s", type(exc).__name__)
        return dict(config or {})


def shutdown_tracing() -> None:
    """Flush pending observations on graceful application shutdown."""
    global _client
    with _lock:
        client, _client = _client, None
    if client is not None:
        try:
            client.shutdown()
        except Exception as exc:  # noqa: BLE001 - shutdown must complete if the collector is unavailable
            logger.warning("Langfuse shutdown failed: %s", type(exc).__name__)
