#!/usr/bin/env bash
# Kernel — one-line installer
# Usage: curl -fsSL https://raw.githubusercontent.com/fabiopacifici-bot/kernel/main/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/fabiopacifici-bot/kernel.git"
INSTALL_DIR="${KERNEL_DIR:-$HOME/.kernel-agent}"
BRANCH="${KERNEL_BRANCH:-main}"

echo ""
echo "  🦞 Kernel — Local-First AI Agent"
echo "  Installing into: $INSTALL_DIR"
echo ""

# Requirements check
command -v python3 >/dev/null 2>&1 || { echo "❌ Python 3.11+ required"; exit 1; }
command -v git >/dev/null 2>&1 || { echo "❌ git required"; exit 1; }

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "❌ Python 3.11+ required (found $PY_VERSION)"; exit 1
fi

# Clone
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "📦 Updating existing install at $INSTALL_DIR..."
  git -C "$INSTALL_DIR" pull origin "$BRANCH"
else
  echo "📦 Cloning Kernel..."
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Python deps
echo "📦 Installing Python dependencies..."
python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt

# .env setup
if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    cp .env.example .env
    echo ""
    echo "  ⚙️  Edit .env to add your Telegram bot token:"
    echo "     MICROCLAW_TELEGRAM_BOT_TOKEN=<your token>"
    echo "     MICROCLAW_TELEGRAM_CHAT_ID=<your chat id>"
    echo ""
    echo "  Get a bot token from @BotFather on Telegram."
    echo ""
  fi
fi

# CLI symlink
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/microclaw" "$HOME/.local/bin/kernel" 2>/dev/null && \
  echo "  ✅ CLI available: kernel" || true

echo ""
echo "  ✅ Kernel installed at $INSTALL_DIR"
echo ""
echo "  Next steps:"
echo "    1. Edit $INSTALL_DIR/.env — add your Telegram bot token"
echo "    2. cd $INSTALL_DIR && bash start.sh"
echo ""
echo "  Or use the one-liner:"
echo "    KERNEL_DIR=$INSTALL_DIR bash start.sh"
echo ""
