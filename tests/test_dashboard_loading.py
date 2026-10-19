from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from agent.api import dashboard_routes as routes

THREAD = {
    "thread_id": "thread-a", "repo_owner": "owner", "repo_name": "repo",
    "latest_run_status": "completed", "title": "example",
}


@pytest.fixture(autouse=True)
def feedback_without_network(monkeypatch):
    monkeypatch.setattr(routes, "thread_feedback", lambda _: {})


def test_sidebar_listing_never_loads_checkpoint_history():
    with patch.object(routes, "list_tasks", return_value=[THREAD]), \
         patch.object(routes, "visible_checkpoint_messages", side_effect=AssertionError("history requested")):
        result = routes.dashboard_threads()
    assert result[0]["id"] == "thread-a"
    assert "messages" not in result[0]


def test_stream_metadata_never_loads_checkpoint_history():
    with patch.object(routes, "_message_payload", side_effect=AssertionError("history requested")):
        assert routes._thread_meta_payload(THREAD)["id"] == "thread-a"


def test_thread_detail_still_loads_history_and_tool_cards():
    history = [{"message_id": "call-a", "author": "agent", "tool": {
        "tool_name": "execute", "status": "completed", "output": "exit_code=0",
    }}]
    with patch.object(routes, "get_task", return_value=THREAD), \
         patch.object(routes, "visible_checkpoint_messages", return_value=history) as read:
        result = routes.dashboard_thread_detail("thread-a")
    read.assert_called_once_with("thread-a", include_tools=True, answer_ids=set())
    assert result["messages"][0]["chunks"][0]["kind"] == "tool"


def test_unavailable_history_is_retryable_and_not_an_empty_success():
    from agent.core.checkpoint_history import CheckpointHistoryError
    with patch.object(routes, "get_task", return_value=THREAD), \
         patch.object(routes, "visible_checkpoint_messages", side_effect=CheckpointHistoryError("历史存储暂不可用")), \
         pytest.raises(HTTPException) as error:
        routes.dashboard_thread_detail("thread-a")
    assert error.value.status_code == 503
    assert error.value.headers["Retry-After"] == "3"


def test_checkpoint_initialization_and_query_failures_are_visible():
    from agent.core import checkpoint_history
    for cause in (RuntimeError("private credentials"), OSError("private URI")):
        with patch.object(checkpoint_history, "get_checkpointer", side_effect=cause), \
             pytest.raises(checkpoint_history.CheckpointHistoryError) as error:
            checkpoint_history.visible_checkpoint_messages("thread-a")
        assert "private" not in str(error.value)
    saver = Mock()
    saver.get_delta_channel_history.side_effect = RuntimeError("private URI")
    with patch.object(checkpoint_history, "get_checkpointer", return_value=saver), \
         pytest.raises(checkpoint_history.CheckpointHistoryError) as error:
        checkpoint_history.visible_checkpoint_messages("thread-a")
    assert "private" not in str(error.value)
