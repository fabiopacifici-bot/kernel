#!/usr/bin/env bash
# Kernel startup script — API + Telegram bot (model server survives restarts)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOCKET_PATH="/tmp/kernel_model.sock"
MODEL_SERVER_LOG="/tmp/kernel_model_server.log"
API_LOG="/tmp/microclaw_api.log"

# ---------------------------------------------------------------------------
# 1. Kill ONLY the API / bot process (port 8769) — NOT the model server
# ---------------------------------------------------------------------------
echo "[kernel] Stopping API on port 8769 (if running)..."
fuser -k 8769/tcp 2>/dev/null || true
sleep 1

# ---------------------------------------------------------------------------
# 2. Source env + activate venv
# ---------------------------------------------------------------------------
set -a; source "$REPO/.env"; set +a
source "$REPO/.venv/bin/activate"

# Print version
GIT_VERSION=$(git -C "$REPO" describe --tags --abbrev=0 2>/dev/null || echo "")
PY_VERSION=$(python3 -c "from src.version import __version__; print(__version__)" 2>/dev/null || echo "unknown")
VERSION=${GIT_VERSION:-$PY_VERSION}
echo "[kernel] Version: $VERSION (git: ${GIT_VERSION:-none}, code: $PY_VERSION)"

# ---------------------------------------------------------------------------
# 3. Run workspace setup
# ---------------------------------------------------------------------------
cd "$REPO/src"
python3 -c "import sys; sys.path.insert(0,'$REPO/src'); from setup import setup_workspace; setup_workspace()"

# ---------------------------------------------------------------------------
# 4. Model server — start only if not already running
# ---------------------------------------------------------------------------
_is_model_server_alive() {
    # Quick Python check using model_client.is_server_running()
    python3 -c "
import sys; sys.path.insert(0, '$REPO/src')
from model_client import is_server_running
sys.exit(0 if is_server_running() else 1)
" 2>/dev/null
}

if _is_model_server_alive; then
    echo "[kernel] Model server already running on $SOCKET_PATH — skipping model load"
else
    echo "[kernel] Starting model server (this may take 3-5 min on first boot)..."
    # Remove stale socket if present
    rm -f "$SOCKET_PATH"

    nohup python3 "$REPO/src/model_server.py" --config "$REPO/config.yaml" \
        >> "$MODEL_SERVER_LOG" 2>&1 &
    MODEL_SERVER_PID=$!
    echo "[kernel] Model server PID: $MODEL_SERVER_PID"

    # Wait up to 300 seconds for socket to be ready
    echo "[kernel] Waiting for model server socket..."
    WAITED=0
    while ! _is_model_server_alive; do
        if [ $WAITED -ge 300 ]; then
            echo "[kernel] ERROR: Model server did not become ready in 300s. Check $MODEL_SERVER_LOG"
            exit 1
        fi
        sleep 5
        WAITED=$((WAITED + 5))
        echo "[kernel] ... ${WAITED}s elapsed"
    done
    echo "[kernel] Model server ready after ${WAITED}s"
fi

# ---------------------------------------------------------------------------
# 5. Launch API (Telegram bot starts as daemon thread inside uvicorn startup)
# ---------------------------------------------------------------------------
nohup python3 -m uvicorn api:app --host 0.0.0.0 --port 8769 >> "$API_LOG" 2>&1 &
echo "[kernel] Started API PID $! on :8769 (log: $API_LOG)"
