"""将用户负反馈可靠投递到 Langfuse 的 err-queue，供人工确认问题。

用户提交评价时只写 MongoDB，本模块由后台任务调用，不阻塞回答或评价接口。
队列投递与 AI 评分、用户评分同步独立，失败后依靠持久化状态和租约恢复。
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from pymongo import ReturnDocument

QUEUE_NAME = "err-queue"
# 人工结论单独记录，避免把用户的不满意直接当成已确认的回答错误。
REVIEW_SCORE_NAME = "feedback_issue_confirmed"
# 由本地持久队列统一重试，避免 SDK 内部重试延长一次租约的占用时间。
REQUEST_OPTIONS = {"max_retries": 0, "timeout_in_seconds": 5}
logger = logging.getLogger("agent.run.feedback")


def _pages(fetch):
    """统一遍历分页接口，防止只查第一页时漏掉已有队列或已投递条目。"""
    page = 1
    while True:
        rows = fetch(page=page, limit=100, request_options=REQUEST_OPTIONS).data
        yield from rows
        if len(rows) < 100:
            return
        page += 1


def _find_queue(client):
    """在当前 Langfuse 凭据所属项目中查找目标队列。"""
    return next((queue for queue in _pages(client.api.annotation_queues.list_queues)
                 if queue.name == QUEUE_NAME), None)


def ensure_error_queue(client):
    """优先复用同名队列；不存在时创建人工评分配置和队列。

    已有队列的评分维度、复核人员由运营维护，这里不覆盖其配置。
    """
    queue = _find_queue(client)
    if queue is not None:
        return queue
    # 已归档的评分配置不能作为新队列的有效评分维度。
    config = next((row for row in _pages(client.api.score_configs.get)
                   if row.name == REVIEW_SCORE_NAME and not row.is_archived), None)
    if config is None:
        config = client.api.score_configs.create(
            name=REVIEW_SCORE_NAME, data_type="BOOLEAN",
            description="Human review: 1 confirms an actionable issue; 0 does not. User feedback is a signal, not a verdict.",
            request_options=REQUEST_OPTIONS,
        )
    if config.data_type != "BOOLEAN":
        raise ValueError("Incompatible feedback_issue_confirmed score config")
    try:
        return client.api.annotation_queues.create_queue(
            name=QUEUE_NAME, score_config_ids=[config.id],
            description="AICODING answers marked not_helpful by users. Inspect the trace and user_helpfulness reason before adjudicating.",
            request_options=REQUEST_OPTIONS,
        )
    except Exception:
        # 创建可能已成功但响应丢失，也可能被其他 Worker 抢先创建；先查询确认。
        queue = _find_queue(client)
        if queue is not None:
            return queue
        raise


def _find_item(client, queue_id, trace_id):
    """按 Trace 查找复核条目，同时匹配对象类型，避免与 Session 等对象混淆。"""
    def fetch(**kwargs):
        return client.api.annotation_queues.list_queue_items(queue_id, **kwargs)

    # 包括 COMPLETED 条目：重试投递不能重新打开已经完成人工复核的任务。
    return next((item for item in _pages(fetch)
                 if item.object_id == trace_id and item.object_type == "TRACE"), None)


def _ensure_item(client, queue_id, trace_id):
    """查询后再创建；发生不确定写入时重新查询，降低重复入队风险。"""
    item = _find_item(client, queue_id, trace_id)
    if item is not None:
        return item
    try:
        return client.api.annotation_queues.create_queue_item(
            queue_id, object_id=trace_id, object_type="TRACE", request_options=REQUEST_OPTIONS,
        )
    except Exception:
        # 超时不等于写入失败；先确认服务端是否已有该条目，再决定是否继续重试。
        item = _find_item(client, queue_id, trace_id)
        if item is not None:
            return item
        raise


def sync_negative_feedback_queue() -> None:
    """每批最多投递20条负反馈，不要求 AI 评分或用户评分已同步。

    历史记录可能没有 err_queue_* 字段，查询条件允许其直接参与补投。
    缺少有效 Trace 的记录暂不认领，避免错误关联到整个聊天会话。
    """
    from agent.core.answer_feedback import collection
    from agent.core.observability import get_langfuse_client

    client = get_langfuse_client()
    if client is None:
        return
    mongo = collection()
    queue = None
    for _ in range(20):
        now, owner = datetime.now(UTC), str(uuid.uuid4())
        # 原子认领防止多个 Worker 同时处理；进程退出后，过期租约允许再次认领。
        # next_attempt_at 单独控制重试时间，避免失败记录在一个批次内反复抢占。
        doc = mongo.find_one_and_update(
            {"rating": "not_helpful", "trace_id": {"$type": "string", "$ne": ""},
             "err_queue_sync_status": {"$ne": "synced"},
             "$and": [
                 {"$or": [{"err_queue_lease_until": {"$exists": False}},
                          {"err_queue_lease_until": {"$lte": now}}]},
                 {"$or": [{"err_queue_next_attempt_at": {"$exists": False}},
                          {"err_queue_next_attempt_at": {"$lte": now}}]},
             ]},
            {"$set": {"err_queue_sync_status": "running", "err_queue_owner": owner,
                      "err_queue_lease_until": now + timedelta(minutes=5)},
             "$inc": {"err_queue_attempts": 1}},
            sort=[("err_queue_next_attempt_at", 1), ("submitted_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if doc is None:
            return
        # 回写必须仍属于本次认领者，防止过期 Worker 覆盖后来 Worker 的处理结果。
        selector = {"_id": doc["_id"], "err_queue_owner": owner, "err_queue_sync_status": "running"}
        try:
            if queue is None:
                # 同一批复用队列信息；没有待处理记录时不会创建空队列。
                queue = ensure_error_queue(client)
            item = _ensure_item(client, queue.id, doc["trace_id"])
            # 仅在服务端确认条目存在后标记成功，并保存定位人工复核任务所需的ID。
            mongo.update_one(selector, {
                "$set": {"err_queue_sync_status": "synced", "err_queue_id": queue.id,
                         "err_queue_item_id": item.id, "err_queue_synced_at": datetime.now(UTC)},
                "$unset": {"err_queue_owner": "", "err_queue_lease_until": "",
                           "err_queue_next_attempt_at": "", "err_queue_last_error_type": ""},
            })
        except Exception as exc:  # noqa: BLE001 - keep external delivery failures durable for retry
            # 采用指数退避，最长等待30分钟；保留反馈原文，只记录异常类型以免泄密。
            delay = min(1800, 30 * 2 ** min(doc.get("err_queue_attempts", 1), 6))
            mongo.update_one(selector, {
                "$set": {"err_queue_sync_status": "pending", "err_queue_last_error_type": type(exc).__name__,
                         "err_queue_next_attempt_at": datetime.now(UTC) + timedelta(seconds=delay)},
                "$unset": {"err_queue_owner": "", "err_queue_lease_until": ""},
            })
            logger.warning("err-queue delivery pending: question_id=%s error=%s", doc["_id"], type(exc).__name__)
            # 结束本批，下一轮重新查询队列，以处理队列被删除或服务不可用等情况。
            return
