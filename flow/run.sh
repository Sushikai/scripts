#!/bin/bash
# flow 一键启动(LAN 内访问)
set -e

cd "$(dirname "$0")"
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"

PORT="${FLOW_PORT:-8810}"
echo ">>> flow starting on http://0.0.0.0:${PORT}"
exec python3 -m uvicorn backend.main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --log-level info \
    --no-access-log