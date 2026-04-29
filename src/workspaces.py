"""
workspaces.py — Multi-workspace access for Kernel.
Kernel's own workspace is ~/.kernel/workspace (read/write).
Other agent workspaces are read-only unless Olly is offline + user approved.
"""
from pathlib import Path
import os

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

KERNEL_WORKSPACE = Path.home() / ".kernel" / "workspace"

KNOWN_WORKSPACES = {
    "kernel":    {"path": str(KERNEL_WORKSPACE), "access": "rw"},
    "olly":      {"path": "~/.openclaw/workspace", "access": "ro"},
    "marketing": {"path": "~/.openclaw/workspace-marketing", "access": "ro"},
    "legal":     {"path": "~/.openclaw/workspace-legal", "access": "ro"},
    "hack":      {"path": "~/.openclaw/workspace-hack", "access": "ro"},
    "invest":    {"path": "~/.openclaw/workspace-invest", "access": "ro"},
}

OPENCLAW_HEALTH_URL = "http://localhost:18789/health"


def olly_online() -> bool:
    if not _HAS_REQUESTS:
        return False
    try:
        r = _requests.get(OPENCLAW_HEALTH_URL, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def get_workspace(name: str = "kernel") -> dict:
    return KNOWN_WORKSPACES.get(name, KNOWN_WORKSPACES["kernel"])


def can_write(workspace_name: str, user_approved: bool = False) -> tuple:
    """Returns (can_write: bool, reason: str)"""
    ws = KNOWN_WORKSPACES.get(workspace_name)
    if not ws:
        return False, f"Unknown workspace: {workspace_name}"
    if ws["access"] == "rw":
        return True, "own workspace"
    if not user_approved:
        return False, f"{workspace_name} workspace is read-only — request approval first"
    if olly_online():
        return False, f"Olly is online — use Olly to modify {workspace_name} workspace"
    return True, f"Olly offline + user approved — write access granted to {workspace_name}"


def list_workspaces() -> list:
    result = []
    for name, ws in KNOWN_WORKSPACES.items():
        path = Path(ws["path"])
        result.append({
            "name": name,
            "path": ws["path"],
            "access": ws["access"],
            "exists": path.exists(),
        })
    return result
