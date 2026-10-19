"""Skill discovery and bounded imports; importing never executes bundled code."""
from __future__ import annotations

import io
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from threading import RLock
from urllib.parse import urlsplit

import requests
import yaml

from agent.core import settings
from agent.env_utils import get_env
from agent.tools.safe_http import request_with_safe_redirects

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SKILL_BYTES = 128 * 1024
MAX_FILES = 500
_IMPORT_LOCK = RLock()
_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


class SkillImportError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_skill(content: bytes) -> dict[str, str]:
    if len(content) > MAX_SKILL_BYTES:
        raise SkillImportError("SKILL.md 不能超过 128 KB")
    try:
        text = content.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise SkillImportError("SKILL.md 必须使用 UTF-8 编码") from exc
    match = re.match(r"\A---\n(.*?)\n---(?:\n|$)(.*)\Z", text, re.S)
    if not match:
        raise SkillImportError("缺少 YAML 头部：SKILL.md 必须包含 name 和 description")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise SkillImportError("SKILL.md 的 YAML 头部格式不正确") from exc
    if not isinstance(metadata, dict):
        raise SkillImportError("Skill 元数据必须是对象")
    name, description = metadata.get("name"), metadata.get("description")
    if not isinstance(name, str) or len(name) > 64 or not _NAME.fullmatch(name):
        raise SkillImportError("name 必须为 1—64 位小写字母、数字和单个连字符")
    if name.split(".")[0].upper() in _RESERVED:
        raise SkillImportError("Skill 名称不能使用系统保留名称")
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise SkillImportError("description 必须为 1—1024 字的说明")
    if not match.group(2).strip():
        raise SkillImportError("SKILL.md 缺少技能正文")
    return {"name": name, "description": description.strip()}


def _safe_path(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in {"", "."}]
    if (normalized.startswith("/") or "\0" in normalized
            or not parts or any(p == ".." or re.search(r'[<>:"|?*]', p) or p.endswith((".", " "))
                   or p.split(".")[0].upper() in _RESERVED for p in parts)):
        raise SkillImportError("压缩包包含不安全路径")
    return PurePosixPath(normalized)


def _archive_files(data: bytes) -> dict[PurePosixPath, bytes]:
    files: dict[PurePosixPath, bytes] = {}
    used: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_FILES:
                raise SkillImportError(f"压缩包最多包含 {MAX_FILES} 个条目")
            for info in archive.infolist():
                path = _safe_path(info.filename)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    raise SkillImportError("不支持符号链接或特殊文件")
                if info.is_dir():
                    continue
                key = str(path).casefold()
                if key in used:
                    raise SkillImportError("压缩包包含重复路径")
                used.add(key)
                total += info.file_size
                if info.file_size > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
                    raise SkillImportError("压缩包展开后过大", 413)
                if info.flag_bits & 1:
                    raise SkillImportError("不支持加密压缩包")
                with archive.open(info) as source:
                    body = source.read(MAX_FILE_BYTES + 1)
                if len(body) != info.file_size or len(body) > MAX_FILE_BYTES:
                    raise SkillImportError("压缩包文件大小不正确", 413)
                files[path] = body
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
        raise SkillImportError("无法读取 ZIP/Skill 压缩包") from exc
    return files


class SkillRegistry:
    def __init__(self, root: Path | None = None):
        workspace = Path(get_env("LOCAL_SHELL_WORKSPACE") or settings.WORKSPACE_ROOT)
        self.root = (root if root is not None else workspace / "skills").resolve()

    def list(self) -> list[dict]:
        if not self.root.exists():
            return []
        result = []
        with _IMPORT_LOCK:
            for folder in sorted(self.root.iterdir()):
                entry = folder / "SKILL.md"
                if not folder.is_dir() or folder.is_symlink() or not entry.is_file() or entry.is_symlink():
                    continue
                try:
                    with entry.open("rb") as source:
                        metadata = parse_skill(source.read(MAX_SKILL_BYTES + 1))
                    result.append({**metadata, "path": f"/skills/{folder.name}/SKILL.md", "valid": True})
                except (SkillImportError, OSError) as exc:
                    # Preserve legacy directories; a bad item must not hide working skills.
                    result.append({"name": folder.name, "description": "", "valid": False,
                                   "error": str(exc) if isinstance(exc, SkillImportError) else "无法读取技能文件",
                                   "path": f"/skills/{folder.name}/SKILL.md"})
        return result

    def install(self, data: bytes, *, filename: str = "skill.zip", replace: bool = False) -> list[dict]:
        if len(data) > MAX_UPLOAD_BYTES:
            raise SkillImportError("上传文件不能超过 10 MB", 413)
        if not data:
            raise SkillImportError("上传文件为空")
        if zipfile.is_zipfile(io.BytesIO(data)):
            files = _archive_files(data)
        elif filename.lower().endswith(".md"):
            files = {PurePosixPath("SKILL.md"): data}
        else:
            raise SkillImportError("请上传 ZIP、.skill 压缩包或 SKILL.md 文件")
        entries = [path for path in files if path.name == "SKILL.md"]
        if not entries:
            raise SkillImportError("压缩包中没有找到 SKILL.md")
        roots = [entry.parent for entry in entries]
        if any(a != b and a in b.parents for a in roots for b in roots):
            raise SkillImportError("不支持相互嵌套的 Skill 目录，请分别打包")
        plans = []
        for entry in entries:
            metadata = parse_skill(files[entry])
            if any(item[0]["name"] == metadata["name"] for item in plans):
                raise SkillImportError("压缩包包含同名 Skill")
            plans.append((metadata, entry.parent))
        self.root.mkdir(parents=True, exist_ok=True)
        with _IMPORT_LOCK, tempfile.TemporaryDirectory(prefix=".skill-import-", dir=self.root.parent) as temporary:
            staging = Path(temporary)
            for metadata, parent in plans:
                target = self.root / metadata["name"]
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    raise SkillImportError("目标 Skill 路径不允许覆盖")
                if target.exists() and not replace:
                    raise SkillImportError(f"Skill {metadata['name']} 已存在；勾选覆盖同名技能后重试", 409)
                candidate = staging / "new" / metadata["name"]
                candidate.mkdir(parents=True)
                for path, content in files.items():
                    if path.is_relative_to(parent):
                        destination = candidate / path.relative_to(parent)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(content)
            backups: list[tuple[Path, Path]] = []
            installed: list[Path] = []
            try:
                for metadata, _ in plans:
                    target = self.root / metadata["name"]
                    if target.exists():
                        backup = staging / "old" / metadata["name"]
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        target.rename(backup)
                        backups.append((target, backup))
                    (staging / "new" / metadata["name"]).rename(target)
                    installed.append(target)
            except OSError:
                for target in reversed(installed):
                    shutil.rmtree(target)
                for target, backup in reversed(backups):
                    backup.rename(target)
                raise SkillImportError("写入 Skill 失败，已恢复原有技能", 500) from None
        return [{**metadata, "path": f"/skills/{metadata['name']}/SKILL.md"} for metadata, _ in plans]

    def import_url(self, url: str, *, replace: bool = False) -> list[dict]:
        try:
            parsed = urlsplit(url)
            _ = parsed.port
        except ValueError:
            raise SkillImportError("下载地址格式不正确") from None
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise SkillImportError("请输入不带账号密码的 HTTP/HTTPS 下载地址")
        response = None
        try:
            response, blocked = request_with_safe_redirects(
                "GET", url, timeout=15, stream=True,
                headers={"User-Agent": "AICoding-Skill-Importer/1.0"},
            )
            if blocked:
                raise SkillImportError("下载地址被拦截：请使用可公开访问的地址，不支持内网地址")
            if response is None:
                raise SkillImportError("未收到下载响应", 502)
            response.raise_for_status()
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
                raise SkillImportError("下载文件不能超过 10 MB", 413)
            data = bytearray()
            deadline = time.monotonic() + 45
            for chunk in response.iter_content(chunk_size=65536):
                if time.monotonic() > deadline:
                    raise SkillImportError("下载超时，请先下载到本地再上传", 504)
                data.extend(chunk)
                if len(data) > MAX_UPLOAD_BYTES:
                    raise SkillImportError("下载文件不能超过 10 MB", 413)
            filename = "skill.zip" if zipfile.is_zipfile(io.BytesIO(data)) else "SKILL.md"
            return self.install(bytes(data), filename=filename, replace=replace)
        except requests.RequestException:
            raise SkillImportError("网络下载失败，请检查链接，或先下载到本地再上传", 502) from None
        finally:
            if response is not None:
                response.close()
