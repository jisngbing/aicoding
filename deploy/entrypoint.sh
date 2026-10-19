#!/bin/sh
set -eu
mkdir -p /app/ai_workspace/skills /app/ai_workspace/projects /app/logs /app/data
cp -rn /app/agent/skills/. /app/ai_workspace/skills/
if [ ! -x /app/ai_workspace/runtimes/python/default/.venv/bin/python ]; then
    python -m venv --system-site-packages /app/ai_workspace/runtimes/python/default/.venv
fi
exec python -m uvicorn agent.app:app --host 0.0.0.0 --port 12024
