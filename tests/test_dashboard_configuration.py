import pytest
from fastapi import HTTPException

from agent.api import dashboard_routes as routes


def test_dashboard_exposes_configured_repository_and_user(monkeypatch):
    monkeypatch.setattr(routes, "DEFAULT_REPO_URL", "https://gitee.com/team/project.git")
    monkeypatch.setattr(routes, "get_main_model_name", lambda: "model")
    monkeypatch.setattr(routes, "get_env", lambda key, default="": "team-user" if key == "DASHBOARD_USER_LOGIN" else default)
    assert routes.dashboard_options()["default_repo_url"] == "https://gitee.com/team/project.git"
    assert routes.dashboard_me()["login"] == "team-user"


def test_repository_is_explicit_and_existing_threads_keep_their_repository(monkeypatch):
    monkeypatch.setattr(routes, "DEFAULT_REPO_URL", "")
    with pytest.raises(HTTPException) as error:
        routes._normalize_dashboard_repo_url(None)
    assert error.value.status_code == 400
    assert "DEFAULT_REPO_URL" in error.value.detail
    assert routes._normalize_dashboard_repo_url(None, fallback="team/existing") == "https://gitee.com/team/existing.git"
    assert routes._normalize_dashboard_repo_url("team/new") == "https://gitee.com/team/new.git"
