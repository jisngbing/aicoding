from __future__ import annotations

from datetime import date
from pathlib import Path

from agent.env_utils import get_env

# 项目根目录由源码位置确定，当前为 aicoding。
# 后续所有本项目自己的数据文件、日志文件都默认放在这个目录下面，
# 避免 LangGraph dev 或第三方工具把文件散落到隐藏目录中。
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# SQLite 数据目录：默认使用项目内 data/。
# 用户仍然可以通过 .env 覆盖，但课程版推荐显式落在项目目录中，
# 方便讲课时直接打开 SQLite 文件观察 checkpoint 和业务 Store 的区别。
DATA_DIR = Path(get_env("LX_AICODING_DATA_DIR", str(PROJECT_ROOT / "data"))).resolve()
CHECKPOINT_DB_PATH = Path(
    get_env("CHECKPOINT_DB_PATH", str(DATA_DIR / "checkpoints.sqlite"))
).resolve()
STORE_DB_PATH = Path(get_env("STORE_DB_PATH", str(DATA_DIR / "store.sqlite"))).resolve()
LANGGRAPH_STORE_DB_PATH = Path(
    get_env("LANGGRAPH_STORE_DB_PATH", str(DATA_DIR / "langgraph_store.sqlite"))
).resolve()

# SQLite 路径仅用于迁移旧数据；运行时的长期记忆和 checkpoint 使用 MongoDB。
MONGODB_URI = get_env("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_USERNAME = get_env("MONGODB_USERNAME")
MONGODB_PASSWORD = get_env("MONGODB_PASSWORD")
MONGODB_AUTH_SOURCE = get_env("MONGODB_AUTH_SOURCE", "admin")
MONGODB_DATABASE = get_env("MONGODB_DATABASE", "aicoding")
MONGODB_MEMORY_COLLECTION = get_env("MONGODB_MEMORY_COLLECTION", "memories")
MONGODB_CHECKPOINT_COLLECTION = get_env("MONGODB_CHECKPOINT_COLLECTION", "checkpoints")
MONGODB_WRITES_COLLECTION = get_env("MONGODB_WRITES_COLLECTION", "checkpoint_writes")
MONGODB_FEEDBACK_COLLECTION = get_env("MONGODB_FEEDBACK_COLLECTION", "answer_feedback")
MONGODB_TIMEOUT_MS = int(get_env("MONGODB_TIMEOUT_MS", "5000"))

# 日志目录：所有后端日志和 Agent 运行日志都写入项目内 logs/。
# 这样既能在控制台实时看，也能在课后通过日志文件复盘 Agent 做了什么。
LOG_DIR = Path(get_env("LX_AICODING_LOG_DIR", str(PROJECT_ROOT / "logs"))).resolve()
LOG_LEVEL = get_env("LX_AICODING_LOG_LEVEL", "INFO").upper()
LOG_ROTATION_WHEN = get_env("LX_AICODING_LOG_WHEN", "midnight")
LOG_ROTATION_INTERVAL = int(get_env("LX_AICODING_LOG_INTERVAL", "1"))
LOG_RETENTION_DAYS = int(get_env("LX_AICODING_LOG_RETENTION_DAYS", "14"))


def log_date_text(target_date: date | None = None) -> str:
    """返回历史日志文件使用的日期文本。

    TimedRotatingFileHandler 当前写入固定文件名，例如 backend.log；
    历史文件默认追加 YYYY-MM-DD 后缀，例如 backend.log.2026-07-10。
    这个函数保留给日志 API 读取历史日期时使用。
    """

    return (target_date or date.today()).isoformat()


def backend_log_path(target_date: date | None = None) -> Path:
    """返回后端日志路径。

    不传日期时返回当前正在写入的固定文件名；传日期时返回标准轮转后的
    历史文件名。
    """

    if target_date is None:
        return LOG_DIR / "backend.log"
    return LOG_DIR / f"backend.log.{log_date_text(target_date)}"


def agent_log_path(target_date: date | None = None) -> Path:
    """返回 Agent 运行日志路径。"""

    if target_date is None:
        return LOG_DIR / "agent-runs.log"
    return LOG_DIR / f"agent-runs.log.{log_date_text(target_date)}"


BACKEND_LOG_PATH = backend_log_path()
AGENT_LOG_PATH = agent_log_path()

# Agent 操作真实代码仓库时使用的工作区，默认放在项目内 ai_workspace。
# 业务仓库统一放在其 projects 子目录中。
WORKSPACE_ROOT = Path(get_env("AI_WORKSPACE_ROOT", str(PROJECT_ROOT / "ai_workspace"))).resolve()
PROJECTS_DIR = WORKSPACE_ROOT / "projects"

# DeepAgents skills 目录，统一放在工作区的 skills 子目录，
# 这样可以通过 DeepAgents 原生 backend route 暴露为 `/skills/`。
SKILLS_DIR = WORKSPACE_ROOT / "skills"
