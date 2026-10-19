"""Perform a read-only Gitee authentication check without exposing the token."""
from pathlib import Path
import json
import logging
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.tools.gitee_api import _gitee_get, mask_token

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

try:
    account = _gitee_get("/user")
    if not isinstance(account, dict) or not account.get("login"):
        raise RuntimeError("The authenticated user response did not contain a login")
    print(json.dumps({"authenticated": True, "login": account["login"]}, ensure_ascii=False))
except Exception as error:
    print(f"Gitee authentication check failed: {mask_token(str(error))}")
    raise SystemExit(1)
