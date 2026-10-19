from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from agent.env_utils import get_env, require_env

DEFAULT_MAIN_MODEL_MAX_TOKENS = 25600
INTENT_MODEL_MAX_TOKENS = 200


def get_main_model_name() -> str:
    """统一模型创建、页面显示和运行事件中的模型名称。"""
    return get_env("QWEN_MODEL", get_env("MAIN_MODEL", "qwen3.8-flash"))


def make_main_model() -> BaseChatModel:
    """通过 DashScope 的 OpenAI 兼容接口创建千问编码模型。"""

    return init_chat_model(
        model=get_main_model_name(),
        model_provider="openai",
        temperature=1.1,
        api_key=require_env("DASHSCOPE_API_KEY"),
        base_url=get_env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        max_tokens=int(get_env("MAIN_MODEL_MAX_TOKENS", str(DEFAULT_MAIN_MODEL_MAX_TOKENS))),
        streaming=True,
        extra_body={"enable_thinking": False},
        timeout=120,
    )


@lru_cache(maxsize=1)
def make_intent_model() -> BaseChatModel:
    """创建用户意图分类专用模型。

    这个模型和主 Agent 模型使用同一套千问 OpenAI 兼容接口配置，
    但参数目标完全不同：

    - 主 Agent 模型负责长上下文推理、工具调用和流式输出，所以 `streaming=True`，
      `max_tokens` 也比较大。
    - 意图分类模型只负责把用户输入归类为固定任务类型，不需要流式输出，也不应该
      产生长文本，因此使用 `streaming=False`、`temperature=0` 和较小的 `max_tokens`。

    单独提供公共函数的好处是：模型初始化逻辑集中在 `agent.core.model`，业务模块
    `task_intent.py` 只关心“如何分类”，不再自己拼装模型连接参数。
    """

    return init_chat_model(
        model=get_env("INTENT_MODEL", get_main_model_name()),
        model_provider="openai",
        temperature=float(get_env("INTENT_MODEL_TEMPERATURE", "0")),
        api_key=require_env("DASHSCOPE_API_KEY"),
        base_url=get_env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        max_tokens=int(get_env("INTENT_MODEL_MAX_TOKENS", str(INTENT_MODEL_MAX_TOKENS))),
        streaming=False,
        extra_body={"enable_thinking": False},
        timeout=30,
    )
