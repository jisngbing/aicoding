"""Package current code for an in-place Linux update without user data or credentials."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    release_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = ROOT / "data" / "linux-update"
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / f"aicoding-{release_id}.tar.gz"
    files = {}
    for directory in ("agent", "scripts", "deploy", "ui/dist", "docs"):
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if any(part.startswith(".") or part in {"__pycache__", "node_modules"} for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix == ".pyc":
                continue
            files[path.relative_to(ROOT).as_posix()] = path
    for name in ("requirements.txt", "requirements.lock.txt", ".dockerignore"):
        files[name] = ROOT / name
    skill = ROOT / "skills" / "ecommerce-sales-analytics"
    for name in ("SKILL.md", "references/metrics.md", "assets/sales-template.csv", "scripts/sales_report.py"):
        files[f"agent/skills/ecommerce-sales-analytics/{name}"] = skill / name
    manifest = {"release_id": release_id, "created_at": datetime.now(UTC).isoformat(), "sha256": {}}
    with tarfile.open(output, "w:gz") as archive:
        for name, path in sorted(files.items()):
            body = path.read_bytes()
            if path.suffix in {".sh", ".yml", ".conf", ".py", ".txt", ".md"} or path.name == "Dockerfile":
                body = body.replace(b"\r\n", b"\n")
            manifest["sha256"][name] = hashlib.sha256(body).hexdigest()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(body), 0o755 if path.suffix == ".sh" else 0o644
            archive.addfile(entry, io.BytesIO(body))
        body = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        entry = tarfile.TarInfo("release.json")
        entry.size, entry.mode = len(body), 0o644
        archive.addfile(entry, io.BytesIO(body))
    (destination / "latest.json").write_text(json.dumps({"release_id": release_id, "archive": str(output),
                                                        "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}), encoding="utf-8")
    print(f"Release {release_id}: {len(files)} files, {output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
