from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agent.core import settings
from agent.core.model import get_main_model_name
from agent.env_utils import get_env

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    """后端健康检查。

    这个接口用于确认 FastAPI/Uvicorn 服务是否正常运行，
    同时展示课程版最关键的运行目录：工作区、SQLite 文件和日志文件。
    注意这里只返回密钥是否存在，不返回真实 API Key 或 Token。
    """

    return {
        "ok": True,
        "project_root": str(settings.PROJECT_ROOT),
        "workspace_root": str(settings.WORKSPACE_ROOT),
        "checkpoint_backend": "mongodb",
        "memory_backend": "mongodb",
        "mongodb_database": settings.MONGODB_DATABASE,
        "store_db": str(settings.STORE_DB_PATH),
        "log_dir": str(settings.LOG_DIR),
        "backend_log": str(settings.backend_log_path()),
        "agent_log": str(settings.agent_log_path()),
        "model_provider": "qwen",
        "has_dashscope_key": bool(get_env("DASHSCOPE_API_KEY")),
        "qwen_base_url": get_env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "main_model": get_main_model_name(),
        "has_gitee_token": bool(get_env("GITEE_TOKEN")),
    }
