"""验证负反馈入队的补投、去重和异常恢复，全部使用模拟客户端与数据库。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.core import answer_feedback as feedback
from agent.core import feedback_error_queue as queue
from agent.core import observability


def page(*rows):
    return SimpleNamespace(data=list(rows))


@pytest.fixture
def client():
    result = Mock()
    result.api.annotation_queues.list_queues.return_value = page(SimpleNamespace(id="queue-id", name="err-queue"))
    result.api.annotation_queues.list_queue_items.return_value = page()
    result.api.annotation_queues.create_queue_item.return_value = SimpleNamespace(id="item-id")
    return result


def test_existing_queue_keeps_its_configuration(client):
    """已有队列由人工维护，自动投递不应覆盖其评分配置。"""
    assert queue.ensure_error_queue(client).id == "queue-id"
    client.api.score_configs.get.assert_not_called()
    client.api.annotation_queues.create_queue.assert_not_called()


def test_missing_queue_creates_separate_human_review_score(client):
    """人工确认结果与用户原始负反馈使用不同指标。"""
    client.api.annotation_queues.list_queues.return_value = page()
    client.api.score_configs.get.return_value = page()
    client.api.score_configs.create.return_value = SimpleNamespace(id="human-config", data_type="BOOLEAN")
    queue.ensure_error_queue(client)
    assert client.api.score_configs.create.call_args.kwargs["name"] == "feedback_issue_confirmed"
    assert client.api.annotation_queues.create_queue.call_args.kwargs["score_config_ids"] == ["human-config"]


def test_queue_creation_response_lost_reuses_existing_queue(client):
    """模拟服务端已创建队列但响应丢失，重查后复用队列。"""
    client.api.annotation_queues.list_queues.side_effect = [page(), page(SimpleNamespace(id="existing", name="err-queue"))]
    client.api.score_configs.get.return_value = page(SimpleNamespace(
        id="config", name="feedback_issue_confirmed", is_archived=False, data_type="BOOLEAN"))
    client.api.annotation_queues.create_queue.side_effect = TimeoutError()
    assert queue.ensure_error_queue(client).id == "existing"


def test_paginated_lookup_keeps_completed_item_completed(client):
    """跨页找到已完成条目后直接复用，不重新打开复核任务。"""
    other = SimpleNamespace(id="other", object_id="other-trace", object_type="TRACE")
    completed = SimpleNamespace(id="completed", object_id="trace", object_type="TRACE", status="COMPLETED")
    client.api.annotation_queues.list_queue_items.side_effect = [page(*([other] * 100)), page(completed)]
    assert queue._ensure_item(client, "queue-id", "trace").id == "completed"
    client.api.annotation_queues.create_queue_item.assert_not_called()
    client.api.annotation_queues.update_queue_item.assert_not_called()


def test_ambiguous_insert_is_reconciled_before_retry(client):
    """模拟入队成功但请求超时，通过重查确认，避免直接再次创建。"""
    item = SimpleNamespace(id="inserted", object_id="trace", object_type="TRACE")
    client.api.annotation_queues.list_queue_items.side_effect = [page(), page(item)]
    client.api.annotation_queues.create_queue_item.side_effect = TimeoutError()
    assert queue._ensure_item(client, "queue-id", "trace").id == "inserted"
    assert client.api.annotation_queues.create_queue_item.call_count == 1


@pytest.fixture
def mongo(monkeypatch, client):
    result = Mock()
    monkeypatch.setattr(feedback, "collection", lambda: result)
    monkeypatch.setattr(observability, "get_langfuse_client", lambda: client)
    return result


def test_historical_negative_is_claimed_without_ai_or_score_prerequisite(mongo, client):
    """历史负反馈无需已有队列状态或AI评分，仍可被原子认领并投递。"""
    mongo.find_one_and_update.side_effect = [{"_id": "question", "trace_id": "trace", "err_queue_attempts": 1}, None]
    queue.sync_negative_feedback_queue()
    criteria = mongo.find_one_and_update.call_args_list[0].args[0]
    assert criteria["rating"] == "not_helpful"
    assert criteria["trace_id"] == {"$type": "string", "$ne": ""}
    assert criteria["err_queue_sync_status"] == {"$ne": "synced"}
    assert "langfuse_sync_status" not in criteria
    assert "$exists" in criteria["$and"][0]["$or"][0]["err_queue_lease_until"]
    assert "$exists" in criteria["$and"][1]["$or"][0]["err_queue_next_attempt_at"]
    created = client.api.annotation_queues.create_queue_item.call_args
    assert created.args == ("queue-id",)
    assert created.kwargs["object_id"] == "trace"
    assert created.kwargs["object_type"] == "TRACE"
    selector, update = mongo.update_one.call_args.args
    assert selector["err_queue_owner"]
    assert update["$set"]["err_queue_sync_status"] == "synced"
    assert update["$set"]["err_queue_item_id"] == "item-id"
    client.api.scores.create.assert_not_called()


def test_delivery_failure_stays_durable_with_backoff(mongo, client):
    """外部服务失败时保存重试时间并释放租约，不丢弃反馈。"""
    mongo.find_one_and_update.return_value = {"_id": "q", "trace_id": "trace", "err_queue_attempts": 2}
    client.api.annotation_queues.list_queues.side_effect = TimeoutError()
    queue.sync_negative_feedback_queue()
    update = mongo.update_one.call_args.args[1]
    assert update["$set"]["err_queue_sync_status"] == "pending"
    assert update["$set"]["err_queue_last_error_type"] == "TimeoutError"
    assert "err_queue_next_attempt_at" in update["$set"]
    assert "err_queue_lease_until" in update["$unset"]
    assert mongo.find_one_and_update.call_count == 1


def test_no_pending_feedback_does_not_create_queue(mongo, client):
    """后台无待处理记录时不访问队列配置接口。"""
    mongo.find_one_and_update.return_value = None
    queue.sync_negative_feedback_queue()
    client.api.annotation_queues.list_queues.assert_not_called()


def test_disabled_langfuse_leaves_feedback_untouched(mongo, monkeypatch):
    """关闭观测或缺少配置时不认领记录，保留后续恢复投递的机会。"""
    monkeypatch.setattr(observability, "get_langfuse_client", lambda: None)
    queue.sync_negative_feedback_queue()
    mongo.find_one_and_update.assert_not_called()


def test_negative_submit_persists_pending_queue_marker(mongo):
    """负反馈与两条同步路径的待处理标记一起落库。"""
    mongo.find_one.return_value = {"rating": None}
    mongo.find_one_and_update.return_value = {"_id": "q", "rating": "not_helpful", "reason": "不相关"}
    feedback.submit_feedback("t", "q", "not_helpful", "不相关")
    changes = mongo.find_one_and_update.call_args.args[1]["$set"]
    assert changes["err_queue_sync_status"] == "pending"
    assert changes["langfuse_sync_status"] == "pending"


def test_helpful_submit_does_not_request_human_error_review(mongo):
    """正反馈不会被加入人工问题复核队列。"""
    mongo.find_one.return_value = {"rating": None}
    mongo.find_one_and_update.return_value = {"_id": "q", "rating": "helpful"}
    feedback.submit_feedback("t", "q", "helpful", "")
    assert "err_queue_sync_status" not in mongo.find_one_and_update.call_args.args[1]["$set"]
