"""
updater.py — Shared self-update logic for Kernel (CLI + Telegram bot).

Used by:
  - microclaw (CLI)   → /update command
  - telegram_bot.py   → /update command + background version check loop
"""
import os
import subprocess
import importlib.util
from pathlib import Path
from typing import Callable, Optional

REPO_DIR = str(Path(__file__).parent.parent)
_GITHUB_RELEASES_URL = "https://api.github.com/repos/fabiopacifici-bot/kernel/releases/latest"
_GITHUB_TAGS_URL     = "https://api.github.com/repos/fabiopacifici-bot/kernel/tags"


def get_current_version() -> str:
    """Read __version__ from src/version.py on disk (always fresh, no import cache)."""
    ver_path = Path(REPO_DIR) / "src" / "version.py"
    spec = importlib.util.spec_from_file_location("_kernel_version", ver_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.__version__


def fetch_latest_version() -> str:
    """Fetch the latest release tag from GitHub. Returns '' on error."""
    try:
        import requests
        resp = requests.get(_GITHUB_RELEASES_URL, timeout=10,
                            headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            tag = resp.json().get("tag_name", "")
            if tag:
                return tag.lstrip("v")
        # Fall back to tags API
        resp = requests.get(_GITHUB_TAGS_URL, params={"per_page": 100}, timeout=10,
                            headers={"Accept": "application/vnd.github+json"})
        if resp.status_code == 200:
            tags = resp.json()
            if tags:
                try:
                    from packaging.version import Version as _V
                    tags.sort(key=lambda t: _V(t["name"].lstrip("v")), reverse=True)
                except Exception:
                    pass
                return tags[0]["name"].lstrip("v")
    except Exception as e:
        print(f"[updater] version-check error: {e}")
    return ""


def do_update(
    notify: Callable[[str], None],
    restart_fn: Optional[Callable[[], None]] = None,
) -> bool:
    """
    Pull latest from git and restart.

    Args:
        notify:     function(str) called with status messages (print for CLI, send_message for bot)
        restart_fn: called after successful pull to restart the process.
                    If None, uses os._exit(0) (relies on start.sh to relaunch).

    Returns True if update was applied, False otherwise.
    """
    current = get_current_version()
    notify("🔍 Checking for updates...")

    latest = fetch_latest_version()
    if not latest:
        notify("❌ Could not reach GitHub to check for updates.")
        return False

    if latest == current:
        notify(f"✅ Already on latest version (v{current}). Nothing to update.")
        return False

    notify(f"🔄 Updating v{current} → v{latest}... pulling main")

    pull = subprocess.run(
        ["git", "pull", "origin", "main"],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if pull.returncode != 0:
        notify(f"❌ git pull failed:\n{pull.stderr[:300]}")
        return False

    # Read version from disk after pull
    try:
        disk_version = get_current_version()
    except Exception:
        disk_version = latest

    notify(f"✅ Updated to v{disk_version}. Restarting...")

    if restart_fn:
        restart_fn()
    else:
        # Default: exit and let start.sh relaunch
        os._exit(0)

    return True


def check_update_available(current_version: str = "") -> Optional[str]:
    """Return latest version string if an update is available, else None."""
    if not current_version:
        current_version = get_current_version()
    latest = fetch_latest_version()
    if latest and latest != current_version:
        return latest
    return None
