import io
import stat
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.api import skills_routes
from agent.core import skills
from agent.core.skills import SkillImportError, SkillRegistry


def markdown(name="sample-skill", description="解释源码时使用"):
    return f"---\nname: {name}\ndescription: {description}\nmetadata:\n  version: '1'\n---\n\n# Skill\n先读取源码，再解释输入输出。\n".encode()


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return stream.getvalue()


@pytest.fixture
def registry(tmp_path):
    return SkillRegistry(tmp_path / "skills")


def test_install_preserves_metadata_and_relative_resources(registry):
    content = markdown()
    package = archive({"download-main/skills/sample/SKILL.md": content,
                       "download-main/skills/sample/references/info.md": "details",
                       "download-main/skills/sample/scripts/check.py": "raise RuntimeError('must not run')",
                       "download-main/README.md": "outside skill"})
    result = registry.install(package)
    assert result[0]["name"] == "sample-skill"
    root = registry.root / "sample-skill"
    assert (root / "SKILL.md").read_bytes() == content
    assert (root / "references/info.md").read_text() == "details"
    assert not (root / "README.md").exists()
    assert registry.list()[0]["valid"] is True


def test_multiple_skills_and_single_markdown(registry):
    registry.install(archive({"one/SKILL.md": markdown("one"), "two/SKILL.md": markdown("two")}))
    registry.install(markdown("three"), filename="SKILL.md")
    assert {item["name"] for item in registry.list()} == {"one", "two", "three"}


def test_conflict_preserves_existing_and_does_not_partially_install_batch(registry):
    registry.install(markdown("one"), filename="SKILL.md")
    original = (registry.root / "one/SKILL.md").read_bytes()
    with pytest.raises(SkillImportError) as error:
        registry.install(archive({"new/SKILL.md": markdown("new"), "one/SKILL.md": markdown("one", "changed")}))
    assert error.value.status == 409
    assert not (registry.root / "new").exists()
    assert (registry.root / "one/SKILL.md").read_bytes() == original
    registry.install(markdown("one", "changed"), filename="SKILL.md", replace=True)
    assert registry.list()[0]["description"] == "changed"


def test_batch_write_failure_rolls_back_existing_skills(registry, monkeypatch):
    registry.install(archive({"one/SKILL.md": markdown("one"), "two/SKILL.md": markdown("two")}))
    original = {name: (registry.root / name / "SKILL.md").read_bytes() for name in ("one", "two")}
    rename = Path.rename

    def fail_second(path, target):
        if path.name == "two" and path.parent.name == "new":
            raise OSError("simulated write failure")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(SkillImportError, match="恢复原有"):
        registry.install(archive({"one/SKILL.md": markdown("one", "new"),
                                  "two/SKILL.md": markdown("two", "new")}), replace=True)
    for name, body in original.items():
        assert (registry.root / name / "SKILL.md").read_bytes() == body


@pytest.mark.parametrize("path", ["../escape", "/tmp/escape", "C:/escape", "dir\\..\\escape", "dir/file:stream", "CON.txt"])
def test_rejects_unsafe_archive_paths_without_writing(registry, path):
    with pytest.raises(SkillImportError):
        registry.install(archive({"SKILL.md": markdown(), path: "bad"}))
    assert not registry.root.exists()


def test_rejects_symlink(registry):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("SKILL.md", markdown())
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, "/etc/passwd")
    with pytest.raises(SkillImportError, match="符号链接"):
        registry.install(output.getvalue())


@pytest.mark.parametrize("body", [b"no frontmatter", b"---\nname: ../bad\ndescription: x\n---\nbody",
                                    b"---\nname: sample\n---\nbody", b"---\nname: sample\ndescription: x\n---\n"])
def test_rejects_invalid_markdown(registry, body):
    with pytest.raises(SkillImportError):
        registry.install(body, filename="SKILL.md")


def test_listing_keeps_legacy_directory_and_reports_bad_format(registry):
    entry = registry.root / "old-format/SKILL.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("legacy content")
    assert registry.list()[0]["valid"] is False
    assert entry.read_text() == "legacy content"
    registry.install(markdown(), filename="SKILL.md")
    assert len(registry.list()) == 2


def test_rejects_nested_skills(registry):
    with pytest.raises(SkillImportError, match="嵌套"):
        registry.install(archive({"root/SKILL.md": markdown("one"), "root/child/SKILL.md": markdown("two")}))


def test_upload_and_expansion_limits(registry, monkeypatch):
    monkeypatch.setattr(skills, "MAX_UPLOAD_BYTES", 5)
    with pytest.raises(SkillImportError) as error:
        registry.install(b"123456")
    assert error.value.status == 413
    monkeypatch.setattr(skills, "MAX_UPLOAD_BYTES", 10000)
    monkeypatch.setattr(skills, "MAX_EXPANDED_BYTES", 50)
    with pytest.raises(SkillImportError) as error:
        registry.install(archive({"SKILL.md": markdown()}))
    assert error.value.status == 413


def response(body):
    result = Mock()
    result.headers = {}
    result.iter_content.return_value = [body[:20], body[20:]]
    return result


@pytest.mark.parametrize("body", [markdown(), archive({"SKILL.md": markdown()})])
def test_network_import_handles_markdown_and_zip_and_closes_response(registry, body):
    downloaded = response(body)
    with patch.object(skills, "request_with_safe_redirects", return_value=(downloaded, None)) as request:
        assert registry.import_url("https://example.com/download")[0]["name"] == "sample-skill"
    assert request.call_args.kwargs["stream"] is True
    downloaded.close.assert_called_once()


def test_network_rejection_error_and_stream_size_limit(registry, monkeypatch):
    with patch.object(skills, "request_with_safe_redirects", return_value=(None, {"error": "private"})):
        with pytest.raises(SkillImportError, match="拦截"):
            registry.import_url("http://127.0.0.1/file.zip")
    with patch.object(skills, "request_with_safe_redirects", side_effect=requests.Timeout("private token")):
        with pytest.raises(SkillImportError) as error:
            registry.import_url("https://example.com/file.zip")
        assert "private token" not in str(error.value)
    downloaded = response(b"0123456789")
    monkeypatch.setattr(skills, "MAX_UPLOAD_BYTES", 5)
    with patch.object(skills, "request_with_safe_redirects", return_value=(downloaded, None)):
        with pytest.raises(SkillImportError) as error:
            registry.import_url("https://example.com/file.zip")
    assert error.value.status == 413
    downloaded.close.assert_called_once()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:secret@example.com/a.zip", "http://[bad", "https://example.com:99999/file"])
def test_bad_url_never_reaches_network(registry, url):
    with patch.object(skills, "request_with_safe_redirects") as request:
        with pytest.raises(SkillImportError):
            registry.import_url(url)
    request.assert_not_called()


def test_binary_upload_api_and_conflict_and_list(registry, monkeypatch):
    app = FastAPI()
    app.include_router(skills_routes.skills_router)
    monkeypatch.setattr(skills_routes, "SkillRegistry", lambda: registry)
    client = TestClient(app)
    result = client.post("/dashboard/api/skills/upload?filename=SKILL.md", content=markdown())
    assert result.status_code == 200
    assert result.json()["installed"][0]["name"] == "sample-skill"
    assert client.post("/dashboard/api/skills/upload?filename=SKILL.md", content=markdown()).status_code == 409
    assert client.get("/dashboard/api/skills").json()["skills"][0]["valid"]
    monkeypatch.setattr(skills_routes, "MAX_UPLOAD_BYTES", 5)
    assert client.post("/dashboard/api/skills/upload", content=b"123456").status_code == 413


def test_url_api_uses_same_registry(registry, monkeypatch):
    app = FastAPI()
    app.include_router(skills_routes.skills_router)
    monkeypatch.setattr(skills_routes, "SkillRegistry", lambda: registry)
    with patch.object(registry, "import_url", return_value=[{"name": "one"}]) as download:
        result = TestClient(app).post("/dashboard/api/skills/import-url", json={"url": "https://example.com/a.zip", "replace": True})
    assert result.status_code == 200
    download.assert_called_once_with("https://example.com/a.zip", replace=True)


def test_streaming_download_closes_redirect_connections():
    from agent.tools import safe_http
    redirect = Mock()
    redirect.is_redirect = True
    redirect.is_permanent_redirect = False
    redirect.status_code = 302
    redirect.url = "https://example.com/first"
    redirect.headers = {"Location": "/last"}
    final = Mock()
    final.is_redirect = final.is_permanent_redirect = False
    with patch.object(safe_http, "_resolve_and_validate", return_value=(True, "", "example.com", [])), \
         patch.object(safe_http, "_pin_dns", return_value=nullcontext()), \
         patch.object(safe_http.requests, "request", side_effect=[redirect, final]):
        result, blocked = safe_http.request_with_safe_redirects("GET", redirect.url, timeout=5, stream=True)
    assert result is final and blocked is None
    redirect.close.assert_called_once()
    final.close.assert_not_called()


def test_existing_thread_discovers_new_skill_on_next_runtime_turn(registry):
    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from agent.core.streaming_runtime import run_agent_with_event_stream

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    registry.install(markdown("existing"), filename="SKILL.md")
    graph = create_deep_agent(model=Model(responses=[AIMessage(content="第一轮完成"), AIMessage(content="第二轮完成")]),
                              backend=FilesystemBackend(root_dir=registry.root.parent),
                              skills=["/skills/"], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "reload-test"}}
    with patch("agent.core.streaming_runtime.record_event"):
        run_agent_with_event_stream(agent=graph, thread_id="reload-test", run_id="one", content="解释代码")
        assert {s["name"] for s in graph.get_state(config).values["skills_metadata"]} == {"existing"}
        registry.install(markdown("new-import"), filename="SKILL.md")
        run_agent_with_event_stream(agent=graph, thread_id="reload-test", run_id="two", content="继续解释")
    assert {s["name"] for s in graph.get_state(config).values["skills_metadata"]} == {"existing", "new-import"}
