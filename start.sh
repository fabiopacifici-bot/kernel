#!/usr/bin/env bash
# Kernel startup script — single unified process (API + Telegram bot)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Kill any existing instance (one process owns both API + bot)
fuser -k 8769/tcp 2>/dev/null || true
sleep 1

set -a; source "$REPO/.env"; set +a
source "$REPO/.venv/bin/activate"

# Read version from git tag first, fallback to version.py
GIT_VERSION=$(git -C "$REPO" describe --tags --abbrev=0 2>/dev/null || echo "")
PY_VERSION=$(python3 -c "from src.version import __version__; print(__version__)" 2>/dev/null || echo "unknown")
VERSION=${GIT_VERSION:-$PY_VERSION}
echo "[kernel] Version: $VERSION (git: ${GIT_VERSION:-none}, code: $PY_VERSION)"

cd "$REPO/src"
python3 -c "import sys; sys.path.insert(0,'$REPO/src'); from setup import setup_workspace; setup_workspace()"

# Start API server (bot starts as daemon thread inside uvicorn startup event)
nohup python3 -m uvicorn api:app --host 0.0.0.0 --port 8769 > /tmp/microclaw_api.log 2>&1 &
echo "[kernel] Started PID $! (API + Telegram bot)"
