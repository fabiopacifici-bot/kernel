#!/usr/bin/env bash
# Kernel WSL auto-start — single unified process (API + Telegram bot)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
REPO="$(cd "$REPO" && pwd)"

# Kill any existing instance
fuser -k 8769/tcp 2>/dev/null || true
sleep 1

set -a; source "$REPO/.env"; set +a
source "$REPO/.venv/bin/activate"

cd "$REPO/src"
python3 -m uvicorn api:app --host 0.0.0.0 --port 8769 >> /tmp/microclaw_api.log 2>&1 &
echo "[kernel] Started PID $!"
