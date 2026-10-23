"""Check model wiring locally; --live additionally sends short Qwen requests."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.core.models import model
from agent.env_utils import get_env


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    model.make_intent_model.cache_clear()
    with patch.object(model, "init_chat_model") as factory:
        model.make_main_model()
        model.make_intent_model()
        main_config, intent_config = [call.kwargs for call in factory.call_args_list]
        for configuration in (main_config, intent_config):
            assert configuration["model"] == model.get_main_model_name()
            assert configuration["api_key"] == get_env("DASHSCOPE_API_KEY")
            assert configuration["base_url"] == get_env("QWEN_BASE_URL")
            assert configuration["extra_body"] == {"enable_thinking": False}
            assert configuration["model_provider"] == "openai"
        assert main_config["streaming"] is True
        assert intent_config["streaming"] is False
        assert intent_config["max_tokens"] == 200
    model.make_intent_model.cache_clear()
    from agent.api.routes import health
    from agent.api.dashboard_routes import dashboard_options
    assert health()["main_model"] == model.get_main_model_name()
    assert health()["has_dashscope_key"] is True
    assert dashboard_options()["default_agent_model"] == model.get_main_model_name()
    print("Qwen configuration, thinking parameters and dashboard wiring: passed")
    if args.live:
        from agent.core.task_intent import _classify_by_model
        coding_model = model.make_main_model()
        coding_model.max_retries = 0
        chunks = list(coding_model.bind(max_tokens=16).stream("Reply only OK."))
        assert any(chunk.content for chunk in chunks), "Empty streaming response"
        print("Qwen streaming request: passed")
        intent_model = model.make_intent_model()
        intent_model.max_retries = 0
        result = _classify_by_model("请列出本地工作区有哪些项目，不要修改代码。")
        assert result == "inspect", result
        print("Qwen structured intent classification: passed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        key = get_env("DASHSCOPE_API_KEY")
        message = str(error).replace(key, "***") if key else str(error)
        print(f"Qwen check failed ({type(error).__name__}): {message}")
        raise SystemExit(1)
