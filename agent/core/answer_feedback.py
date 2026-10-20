"""MongoDB answer feedback and its durable Langfuse score delivery queue."""
from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from agent.core import settings
from agent.core.persistence import get_mongo_client

logger = logging.getLogger("agent.run.feedback")
current_question: ContextVar[dict[str, Any] | None] = ContextVar("current_question", default=None)


@lru_cache(maxsize=1)
def collection():
    result = get_mongo_client()[settings.MONGODB_DATABASE][settings.MONGODB_FEEDBACK_COLLECTION]
    result.create_index("thread_id")
    result.create_index("langfuse_sync_status")
    # 支持按负反馈类型、投递状态和重试时间查找待人工复核记录。
    result.create_index([("rating", 1), ("err_queue_sync_status", 1), ("err_queue_next_attempt_at", 1)])
    return result


def feedback_payload(document: dict) -> dict:
    return {"question_id": document["_id"], "trace_id": document.get("trace_id"),
            "rating": document.get("rating"), "reason": document.get("reason", ""),
            "submitted_at": document.get("submitted_at")}


def complete_answer(*, answer_id: str | None, run_id: str) -> dict | None:
    context = current_question.get()
    if not context or not answer_id:
        return None
    document = {"_id": context["question_id"], "thread_id": context["thread_id"],
                "trace_id": context["trace_id"], "run_id": run_id, "answer_id": answer_id,
                "completed_at": datetime.now(UTC), "rating": None}
    collection().update_one({"_id": document["_id"]}, {"$setOnInsert": document}, upsert=True)
    return feedback_payload(document)


def thread_feedback(thread_id: str) -> dict[str, dict]:
    return {doc["answer_id"]: feedback_payload(doc)
            for doc in collection().find({"thread_id": thread_id})}


def submit_feedback(thread_id: str, question_id: str, rating: str, reason: str) -> dict:
    from pymongo import ReturnDocument
    reason = reason.strip() if rating == "not_helpful" else ""
    if rating not in {"helpful", "not_helpful"} or (rating == "not_helpful" and not reason):
        raise ValueError("不满意时请填写原因")
    document = collection().find_one({"_id": question_id, "thread_id": thread_id})
    if document is None:
        raise LookupError("回答不存在或尚未完成")
    if document.get("rating"):
        if document["rating"] != rating or document.get("reason", "") != reason:
            raise FileExistsError("此回答已提交评价")
        return feedback_payload(document)
    # 评价与待投递标记在同一次原子更新中保存；此处不调用外部 Langfuse 接口。
    # 只有“没有帮助”进入人工问题队列，“有帮助”仍仅同步原有用户评分。
    document = collection().find_one_and_update(
        {"_id": question_id, "thread_id": thread_id, "rating": None},
        {"$set": {"rating": rating, "reason": reason, "submitted_at": datetime.now(UTC),
                  "langfuse_sync_status": "pending",
                  **({"err_queue_sync_status": "pending"} if rating == "not_helpful" else {})}},
        return_document=ReturnDocument.AFTER,
    )
    if document is None:
        raise FileExistsError("此回答已提交评价，请刷新后查看")
    return feedback_payload(document)


def sync_pending_feedback() -> None:
    from agent.core.observability import _mask_data, get_langfuse_client
    from agent.core.tool_display import get_display_secrets
    from agent.env_utils import get_env
    client = get_langfuse_client()
    if client is None:
        return
    for doc in collection().find({"langfuse_sync_status": "pending", "trace_id": {"$ne": None}}).limit(20):
        try:
            client.api.scores.create(
                id="feedback-" + doc["_id"], trace_id=doc["trace_id"],
                name="user_helpfulness", value=1 if doc["rating"] == "helpful" else 0,
                data_type="BOOLEAN", comment=_mask_data(doc.get("reason", ""), get_display_secrets()),
                environment=get_env("LANGFUSE_TRACING_ENVIRONMENT", "development"),
                metadata={"question_id": doc["_id"], "thread_id": doc["thread_id"], "run_id": doc["run_id"]},
                request_options={"max_retries": 0, "timeout_in_seconds": 5},
            )
            collection().update_one({"_id": doc["_id"]}, {"$set": {
                "langfuse_sync_status": "synced", "langfuse_synced_at": datetime.now(UTC)}})
        except Exception as exc:  # noqa: BLE001 - preserve pending feedback for the next delivery attempt
            logger.warning("Feedback delivery pending: question_id=%s error=%s", doc["_id"], type(exc).__name__)


async def feedback_delivery_loop() -> None:
    """每30秒处理评分同步和负反馈入队，两条路径分别捕获异常。"""
    from agent.core.feedback_error_queue import sync_negative_feedback_queue

    while True:
        for operation in (sync_pending_feedback, sync_negative_feedback_queue):
            try:
                # MongoDB 与 SDK 为同步调用，移到线程执行，避免阻塞 SSE 所在事件循环。
                await asyncio.to_thread(operation)
            except Exception as exc:  # noqa: BLE001 - one delivery path must not block the other
                logger.warning("Feedback delivery unavailable: operation=%s error=%s",
                               operation.__name__, type(exc).__name__)
        await asyncio.sleep(30)
