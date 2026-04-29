#!/usr/bin/env bash
# Kernel WSL auto-start — delegates to start.sh (model-server-aware)
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
REPO="$(cd "$REPO" && pwd)"

exec bash "$REPO/start.sh"
