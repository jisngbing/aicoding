"""Skill management API, separate from the agent's read-only skills tools."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agent.core.skills import MAX_UPLOAD_BYTES, SkillImportError, SkillRegistry

skills_router = APIRouter(prefix="/dashboard/api/skills", tags=["skills"])


class SkillUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    replace: bool = False


def _error(exc: SkillImportError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


@skills_router.get("")
def list_skills():
    return {"skills": SkillRegistry().list(), "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}


@skills_router.post("/upload")
async def upload_skill(request: Request, filename: str = "skill.zip", replace: bool = False):
    # Raw binary upload avoids a new multipart dependency and is bounded while reading.
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="上传文件不能超过 10 MB")
        data.extend(chunk)
    try:
        installed = await run_in_threadpool(SkillRegistry().install, bytes(data), filename=filename, replace=replace)
        return {"installed": installed, "message": "导入成功，下一轮任务自动加载"}
    except SkillImportError as exc:
        raise _error(exc) from None


@skills_router.post("/import-url")
def import_skill_url(body: SkillUrlRequest):
    try:
        installed = SkillRegistry().import_url(body.url.strip(), replace=body.replace)
        return {"installed": installed, "message": "导入成功，下一轮任务自动加载"}
    except SkillImportError as exc:
        raise _error(exc) from None
