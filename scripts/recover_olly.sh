#!/usr/bin/env bash
# recover_olly.sh — Kernel's recovery script for OpenClaw/Olly
# Run this whenever Olly is unresponsive. Safe to run multiple times.
# Usage: bash recover_olly.sh [--restart-only] [--diagnose-only]

set -euo pipefail

OPENCLAW_BIN=$(which openclaw 2>/dev/null || echo "$HOME/.local/bin/openclaw")
GATEWAY_PORT=18789
MAX_WAIT=60
LOG_FILE="/tmp/olly_recovery_$(date +%s).log"

log() { echo "[recover] $*" | tee -a "$LOG_FILE"; }
ok()  { echo "[recover] ✅ $*" | tee -a "$LOG_FILE"; }
err() { echo "[recover] ❌ $*" | tee -a "$LOG_FILE"; }
warn(){ echo "[recover] ⚠️  $*" | tee -a "$LOG_FILE"; }

log "=== Olly Recovery Script — $(date) ==="

# ── 1. Check current state ──────────────────────────────────────────────────
log "Step 1: Checking gateway status..."
GATEWAY_UP=false
if curl -sf "http://localhost:$GATEWAY_PORT/health" >/dev/null 2>&1; then
    ok "Gateway is already responding on :$GATEWAY_PORT"
    GATEWAY_UP=true
else
    warn "Gateway not responding on :$GATEWAY_PORT"
fi

if [[ "${1:-}" == "--diagnose-only" ]]; then
    log "Running openclaw doctor..."
    "$OPENCLAW_BIN" doctor --non-interactive 2>&1 | tee -a "$LOG_FILE" || true
    log "Log saved: $LOG_FILE"
    exit 0
fi

if [[ "$GATEWAY_UP" == "true" && "${1:-}" != "--restart-only" ]]; then
    ok "Gateway already up — no restart needed."
    log "Log saved: $LOG_FILE"
    exit 0
fi

# ── 2. Run doctor ────────────────────────────────────────────────────────────
log "Step 2: Running openclaw doctor..."
"$OPENCLAW_BIN" doctor --non-interactive 2>&1 | tee -a "$LOG_FILE" || warn "Doctor returned non-zero (continuing)"

# ── 3. Stop gateway ──────────────────────────────────────────────────────────
log "Step 3: Stopping gateway..."
"$OPENCLAW_BIN" gateway stop 2>&1 | tee -a "$LOG_FILE" || warn "Stop returned non-zero (may already be stopped)"
sleep 2

# Kill any lingering openclaw processes
pkill -f "openclaw.*gateway" 2>/dev/null || true
sleep 1

# ── 4. Clear stale locks ─────────────────────────────────────────────────────
log "Step 4: Clearing stale session locks..."
find "$HOME/.openclaw/agents" -name "*.lock" -mmin +5 -delete 2>/dev/null && ok "Stale locks cleared" || warn "No locks found or couldn't clear"

# ── 5. Start gateway ─────────────────────────────────────────────────────────
log "Step 5: Starting gateway..."
"$OPENCLAW_BIN" gateway start 2>&1 | tee -a "$LOG_FILE" || { err "Gateway start failed"; cat "$LOG_FILE"; exit 1; }

# ── 6. Wait for gateway to come up ──────────────────────────────────────────
log "Step 6: Waiting for gateway (up to ${MAX_WAIT}s)..."
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    if curl -sf "http://localhost:$GATEWAY_PORT/health" >/dev/null 2>&1; then
        ok "Gateway is UP on :$GATEWAY_PORT after ${ELAPSED}s"
        break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if ! curl -sf "http://localhost:$GATEWAY_PORT/health" >/dev/null 2>&1; then
    err "Gateway did not come up after ${MAX_WAIT}s"
    log "Last 20 lines of openclaw log:"
    journalctl --user -u openclaw 2>/dev/null | tail -20 | tee -a "$LOG_FILE" || true
    log "Log saved: $LOG_FILE"
    exit 1
fi

# ── 7. Verify Telegram channel ───────────────────────────────────────────────
log "Step 7: Checking Telegram channel..."
if "$OPENCLAW_BIN" status 2>&1 | grep -q "Telegram.*ok"; then
    ok "Telegram channel active"
else
    warn "Telegram channel status unclear — check manually"
fi

ok "=== Recovery complete ==="
log "Full log: $LOG_FILE"
cat "$LOG_FILE"
