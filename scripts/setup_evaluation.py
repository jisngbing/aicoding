"""Provision versioned judge prompt and score definitions; no credentials printed."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.core.evaluation import JUDGE_PROMPT, PROMPT_NAME, VERSION, WEIGHTS
from agent.core.observability import get_langfuse_client


def main():
    client = get_langfuse_client()
    if client is None:
        raise RuntimeError("Langfuse not configured")
    existing, page = {}, 1
    while True:
        rows = client.api.score_configs.get(page=page, limit=100).data
        existing.update({row.name: row for row in rows if not row.is_archived})
        if len(rows) < 100:
            break
        page += 1
    configs = {"user_helpfulness": ("BOOLEAN", 1), "execution_success": ("BOOLEAN", 1),
               "tests_passed": ("BOOLEAN", 1), "tool_success_ratio": ("NUMERIC", 1),
               "tool_result_coverage": ("NUMERIC", 1), "answer_quality": ("NUMERIC", 100),
               **{"answer_" + name: ("NUMERIC", 4) for name in WEIGHTS}}
    for name, (kind, upper) in configs.items():
        if name in existing:
            row = existing[name]
            if row.data_type != kind or (kind == "NUMERIC" and (row.min_value != 0 or row.max_value != upper)):
                raise RuntimeError("Existing score config conflicts: " + name)
            continue
        arguments = {"min_value": 0, "max_value": upper} if kind == "NUMERIC" else {}
        client.api.score_configs.create(name=name, data_type=kind, description=f"AICODING evaluation {VERSION}; missing evidence is unscored", **arguments)
    # Read before create so repeated setup does not create unnecessary versions.
    try:
        prompt = client.get_prompt(PROMPT_NAME, label="production", max_retries=0)
    except Exception as exc:
        if getattr(exc, "status_code", None) != 404:
            raise
        prompt = None
    if prompt is None or prompt.prompt != JUDGE_PROMPT:
        prompt = client.create_prompt(name=PROMPT_NAME, prompt=JUDGE_PROMPT, type="text", labels=["production"],
                                      config={"standard_version": VERSION, "weights": WEIGHTS}, tags=["evaluation"],
                                      commit_message="Evaluation standard v1.0")
    # Pin this deployment to a reviewed prompt version; preserve all unrelated .env lines.
    env_path = ROOT / ".env"
    content = env_path.read_text(encoding="utf-8")
    settings = {"EVAL_ENABLED": "true", "EVAL_SAMPLE_RATE": "0.2", "EVAL_PROMPT_VERSION": str(prompt.version),
                "MONGODB_EVALUATION_COLLECTION": "answer_evaluations"}
    import re
    for key, value in settings.items():
        pattern = rf"(?m)^{key}=.*$"
        if re.search(pattern, content):
            content = re.sub(pattern, f"{key}={value}", content)
        else:
            content = content.rstrip() + f"\n{key}={value}\n"
    env_path.write_text(content, encoding="utf-8")
    print(f"Configured {len(configs)} score definitions; prompt version={prompt.version}; evaluation enabled")
    client.shutdown()


if __name__ == "__main__":
    main()
