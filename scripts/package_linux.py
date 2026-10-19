"""Build a deployment archive; keep credentials in ignored local artifacts only."""
from __future__ import annotations

import base64
import hashlib
import io
import os
import secrets
import sqlite3
import tarfile
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get("PACKAGE_SOURCE_ROOT") or ROOT).resolve()


def main() -> None:
    output = ROOT / "data/linux-deployment"
    output.mkdir(parents=True, exist_ok=True)
    values = dotenv_values(SOURCE / ".env")
    allowed = ("DASHSCOPE_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL", "MAIN_MODEL", "GITEE_TOKEN",
               "GITEE_API_BASE_URL", "GITEE_WEB_BASE_URL", "ZHIPU_API_KEY", "MONGODB_USERNAME",
               "MONGODB_PASSWORD", "MONGODB_AUTH_SOURCE", "MONGODB_DATABASE",
               "DEFAULT_REPO_URL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
               "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "DASHBOARD_USER_LOGIN")
    env = {key: values[key] for key in allowed if values.get(key)}
    env.update({
        "MONGODB_URI": "mongodb://rag-mongodb:27017",
        "AI_WORKSPACE_ROOT": "/app/ai_workspace", "LOCAL_SHELL_WORKSPACE": "/app/ai_workspace",
        "LX_AICODING_DATA_DIR": "/app/data", "LX_AICODING_LOG_DIR": "/app/logs",
        "LOCAL_SHELL_SHARED_PYTHON_VENV": "runtimes/python/default/.venv",
        "LOCAL_SHELL_CREATE_PYTHON_VENV": "true", "LOCAL_SHELL_ENABLE_COMMAND_GUARD": "true",
        "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
    })
    public_url = os.environ.get("DEPLOY_PUBLIC_URL") or values.get("DEPLOY_PUBLIC_URL") or "http://localhost/"
    username = os.environ.get("DEPLOY_WEB_USER") or values.get("DEPLOY_WEB_USER") or "aicoding"
    if not username or any(char in username for char in ":\r\n") or any(char in public_url for char in "\r\n"):
        raise ValueError("Invalid deployment URL or web username")
    password = secrets.token_urlsafe(18)
    access = output / "access.txt"
    access.write_text(f"URL: {public_url}\nUsername: {username}\nPassword: {password}\n", encoding="utf-8")
    auth = username + ":{SHA}" + base64.b64encode(hashlib.sha1(password.encode()).digest()).decode() + "\n"
    env_text = "".join(f"{key}='{str(value).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for key, value in env.items())
    snapshot = output / "store.sqlite"
    source = sqlite3.connect((SOURCE / "data/store.sqlite").as_uri() + "?mode=ro", uri=True)
    backup = sqlite3.connect(snapshot)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
    archive = output / "aicoding-linux.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        def add_file(path: Path, name: str) -> None:
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                return
            tar.add(path, arcname=name, recursive=False)
        for directory in ("agent", "scripts", "ui/dist", "ai_workspace/projects"):
            for path in (SOURCE / directory).rglob("*"):
                if path.is_file():
                    relative = path.relative_to(SOURCE)
                    if any(part in {".venv", "node_modules", ".pytest_cache", "__pycache__"} for part in relative.parts):
                        continue
                    if path.name == ".env" or path.suffix == ".pyc":
                        continue
                    override = ROOT / relative
                    # Only overlay the files changed for Linux deployment.
                    if str(relative).replace("\\", "/") in {
                        "agent/backends/local_shell.py", "scripts/check_sandbox.py",
                        "agent/api/dashboard_routes.py", "agent/api/routes.py",
                    }:
                        path = override
                    add_file(path, relative.as_posix())
        for path in (ROOT / "deploy").glob("*"):
            if path.is_file() and not path.name.startswith("."):
                add_file(path, "deploy/" + path.name)
        for name in ("requirements.lock.txt", ".dockerignore"):
            add_file(ROOT / name, name)
        add_file(snapshot, "deploy/seed/store.sqlite")
        for name, content, mode in ((".env-linux", env_text, 0o600), ("deploy/.htpasswd", auth, 0o644)):
            payload = content.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(payload), mode
            tar.addfile(info, io.BytesIO(payload))
    print(f"Deployment archive: {archive.name}; bytes={archive.stat().st_size}")
    print("Login credentials saved to ignored data/linux-deployment/access.txt")


if __name__ == "__main__":
    main()
