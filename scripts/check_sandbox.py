"""Run local sandbox checks without calling a model or a remote repository."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.env_utils import load_environment

load_environment()
from agent.backends.local_shell import LocalShellBackend


def main() -> None:
    backend = LocalShellBackend()
    health = backend.health()
    # Production serves the prebuilt UI through Nginx; Node is optional in the backend.
    required = {key: value for key, value in health["checks"].items() if key != "node_available"}
    assert health["healthy"] and all(required.values()), health
    assert not health["errors"], health
    virtual_path = f"/projects/sandbox-check-{uuid.uuid4().hex}.txt"
    actual_path = backend.root / virtual_path.lstrip("/")
    try:
        assert not backend.write(virtual_path, "sandbox-ready").error
        assert backend.read(virtual_path).file_data["content"] == "sandbox-ready"
        assert not backend.edit(virtual_path, "sandbox-ready", "sandbox-verified").error
        assert backend.read(virtual_path).file_data["content"] == "sandbox-verified"
        assert backend.read("/../outside.txt").error
        assert backend.write("/skills/blocked.txt", "blocked").error
        assert backend.write("/.secrets/blocked.txt", "blocked").error
        assert backend.execute("shutdown /s").exit_code == 126
        assert backend.execute("git --version && echo blocked").exit_code == 126
        for command in ("python --version", "pytest --version", "ruff --version", "git --version", f"type {virtual_path}"):
            result = backend.execute(command, timeout=20)
            assert result.exit_code == 0, (command, result)
    finally:
        actual_path.unlink(missing_ok=True)
    print(json.dumps({"status": "passed", "health": health}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
