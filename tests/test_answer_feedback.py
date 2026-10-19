from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.api import dashboard_routes as routes
from agent.core import answer_feedback as feedback
from agent.core import observability


def document(**changes):
    return {"_id": "question-one", "thread_id": "thread-one", "trace_id": "a" * 32,
            "run_id": "run-one", "answer_id": "answer-one", "rating": None, **changes}


@pytest.fixture
def mongo(monkeypatch):
    collection = Mock()
    monkeypatch.setattr(feedback, "collection", lambda: collection)
    return collection


def test_helpful_needs_no_reason_and_is_saved_once(mongo):
    mongo.find_one.return_value = document()
    mongo.find_one_and_update.return_value = document(rating="helpful", reason="", submitted_at=datetime.now(UTC))
    result = feedback.submit_feedback("thread-one", "question-one", "helpful", "ignored")
    assert result["rating"] == "helpful"
    saved = mongo.find_one_and_update.call_args.args[1]["$set"]
    assert saved["reason"] == ""
    assert saved["langfuse_sync_status"] == "pending"
    mongo.find_one.return_value = document(rating="helpful", reason="")
    feedback.submit_feedback("thread-one", "question-one", "helpful", "")
    assert mongo.find_one_and_update.call_count == 1
    with pytest.raises(FileExistsError):
        feedback.submit_feedback("thread-one", "question-one", "not_helpful", "not relevant")


def test_negative_reason_and_target_validation(mongo):
    with pytest.raises(ValueError):
        feedback.submit_feedback("thread-one", "question-one", "not_helpful", "   ")
    mongo.find_one.return_value = None
    with pytest.raises(LookupError):
        feedback.submit_feedback("other-thread", "question-one", "helpful", "")
    mongo.find_one.assert_called_once_with({"_id": "question-one", "thread_id": "other-thread"})


def test_http_validation_and_no_client_supplied_trace(monkeypatch):
    app = FastAPI()
    app.include_router(routes.dashboard_router)
    submit = Mock(return_value={"question_id": "q", "rating": "helpful"})
    monkeypatch.setattr(routes, "submit_feedback", submit)
    with TestClient(app) as http:
        url = "/dashboard/api/threads/t/questions/q/feedback"
        assert http.post(url, json={"rating": "not_helpful", "reason": " "}).status_code == 422
        assert http.post(url, json={"rating": "helpful"}).status_code == 200
        submit.assert_called_once_with("t", "q", "helpful", "")
        assert http.post(url, json={"rating": "not_helpful", "reason": "x" * 2001}).status_code == 422


def test_completed_answer_retains_question_trace_and_answer_ids(mongo):
    token = feedback.current_question.set({"question_id": "question-one", "thread_id": "thread-one", "trace_id": "a" * 32})
    try:
        result = feedback.complete_answer(answer_id="answer-one", run_id="run-one")
    finally:
        feedback.current_question.reset(token)
    stored = mongo.update_one.call_args.args[1]["$setOnInsert"]
    assert stored["_id"] == result["question_id"] == "question-one"
    assert stored["trace_id"] == "a" * 32
    assert stored["answer_id"] == "answer-one"


def test_delivery_failure_is_retryable_and_score_id_is_stable(mongo, monkeypatch):
    row = document(rating="not_helpful", reason="wrong date", langfuse_sync_status="pending")
    mongo.find.return_value.limit.return_value = [row]
    client = Mock()
    monkeypatch.setattr(observability, "get_langfuse_client", lambda: client)
    monkeypatch.setattr("agent.core.tool_display.get_display_secrets", lambda: ())
    client.api.scores.create.side_effect = TimeoutError()
    feedback.sync_pending_feedback()
    mongo.update_one.assert_not_called()
    client.api.scores.create.side_effect = None
    feedback.sync_pending_feedback()
    kwargs = client.api.scores.create.call_args.kwargs
    assert kwargs["id"] == "feedback-question-one"
    assert kwargs["trace_id"] == "a" * 32
    assert kwargs["value"] == 0
    assert kwargs["comment"] == "wrong date"
    assert kwargs["metadata"]["question_id"] == "question-one"
    assert mongo.update_one.call_args.args[1]["$set"]["langfuse_sync_status"] == "synced"


def test_history_restores_feedback_on_the_right_answer(monkeypatch):
    monkeypatch.setattr(routes, "visible_checkpoint_messages", lambda *a, **k: [
        {"message_id": "answer-one", "author": "agent", "content": "First answer"},
        {"message_id": "answer-two", "author": "agent", "content": "Second answer"},
    ])
    monkeypatch.setattr(routes, "thread_feedback", lambda _: {"answer-one": {"question_id": "question-one", "rating": "helpful"}})
    messages = routes._message_payload({"thread_id": "thread-one"})
    assert messages[0]["feedback"]["question_id"] == "question-one"
    assert "feedback" not in messages[1]


def test_short_completed_answers_and_repeated_answers_keep_distinct_ids(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage
    from agent.core import checkpoint_history
    history = [HumanMessage(content="first", id="q1"), AIMessage(content="OK", id="a1"),
               HumanMessage(content="second", id="q2"), AIMessage(content="OK", id="a2")]
    monkeypatch.setattr(checkpoint_history, "_delta_messages_from_checkpoint", lambda _: history)
    messages = checkpoint_history.visible_checkpoint_messages("t", answer_ids={"a1", "a2"})
    assert [m["message_id"] for m in messages] == ["q1", "a1", "q2", "a2"]
