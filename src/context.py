"""
context.py — Build the Kernel system prompt with rich environmental awareness.
Called once per inference to inject live facts: date, hardware, loaded capabilities,
known services, approval gate rules, user identity, and Olly relationship.
"""
import os
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path


def _system_resources() -> dict:
    """Get CPU load, RAM free, disk free."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.home()))
        return {
            "cpu_pct": round(cpu),
            "ram_free_gb": round(ram.available / (1024**3), 1),
            "ram_total_gb": round(ram.total / (1024**3), 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
        }
    except Exception:
        return {}


def _olly_alive(endpoint: str) -> bool:
    try:
        import requests
        r = requests.get(f"{endpoint}/api/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def _service_status(port: int) -> str:
    try:
        result = subprocess.run(
            f"fuser {port}/tcp 2>/dev/null",
            shell=True, capture_output=True, text=True, timeout=3
        )
        return "up" if result.stdout.strip() else "down"
    except Exception:
        return "unknown"


def _load_user_profile() -> dict:
    """Load user profile from ~/.kernel/workspace/user.json"""
    path = Path.home() / ".kernel" / "workspace" / "user.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _load_notes() -> list[str]:
    """Load active notes/todos from ~/.kernel/workspace/notes/"""
    notes_dir = Path.home() / ".kernel" / "workspace" / "notes"
    notes = []
    if notes_dir.exists():
        for f in sorted(notes_dir.glob("*.md"))[-5:]:
            try:
                content = f.read_text().strip()
                if content:
                    first_line = content.split("\n")[0].lstrip("#").strip()
                    notes.append(f"[{f.stem}] {first_line}")
            except Exception:
                pass
    return notes


def _last_interaction() -> str:
    """Return human-readable last interaction time from memory file mtime."""
    import json
    from datetime import datetime
    mem_file = Path.home() / ".kernel_memory.json"
    if not mem_file.exists():
        return "no prior session"
    try:
        mtime = mem_file.stat().st_mtime
        last = datetime.fromtimestamp(mtime)
        now = datetime.now()
        diff = now - last
        minutes = int(diff.total_seconds() / 60)
        if minutes < 2:
            return "just now (same session)"
        elif minutes < 60:
            return f"{minutes} minutes ago"
        elif minutes < 1440:
            return f"{minutes // 60}h ago"
        else:
            return last.strftime("%b %d at %H:%M")
    except Exception:
        return "unknown"


def _memory_stats() -> dict:
    """Return basic stats about conversation memory."""
    import json
    mem_file = Path.home() / ".kernel_memory.json"
    try:
        data = json.loads(mem_file.read_text())
        msgs = data.get("messages", [])
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        return {"total": len(msgs), "user_turns": len(user_msgs)}
    except Exception:
        return {}


def _load_long_term_memory() -> list[str]:
    """Load long-term memory notes from ~/.kernel/workspace/memory/"""
    mem_dir = Path.home() / ".kernel" / "workspace" / "memory"
    notes = []
    if mem_dir.exists():
        for f in sorted(mem_dir.glob("*.md"))[-3:]:  # last 3 files
            try:
                content = f.read_text().strip()
                if content:
                    # First 300 chars per file
                    notes.append(f"[{f.stem}] {content[:300]}")
            except Exception:
                pass
    return notes


def build_system_prompt(config: dict, skills: list, routines: list, vram_free_fn=None, channel: str = "unknown", sender_name: str = "", agent_ready: bool = True) -> str:
    """
    Build a compact but complete system prompt for Kernel.
    Injected fresh on every triage() call.
    """
    now = datetime.now()
    hostname = platform.node()
    os_name = platform.system()
    kernel_workspace = os.path.expanduser(config.get("kernel_workspace", "~/.kernel/workspace"))
    olly_workspace = config.get("olly_workspace", "")
    openclaw_endpoint = config.get("api", {}).get("openclaw_endpoint", "http://localhost:18789")

    try:
        from version import __version__
    except Exception:
        __version__ = "unknown"

    model_name = config.get("model", {}).get("name", "unknown")
    model_device = config.get("model", {}).get("device", "auto")

    vram_mb = 0
    if vram_free_fn:
        try:
            vram_mb = vram_free_fn()
        except Exception:
            pass

    resources = _system_resources()

    skill_names = [s["name"] for s in skills] if skills else []
    routine_names = [r["name"] for r in routines] if routines else []

    olly_status = "up" if _olly_alive(openclaw_endpoint) else "down"
    fantasia_status = _service_status(8765)
    voice_status = _service_status(8766)

    user = _load_user_profile()
    user_name = user.get("name", "unknown")
    user_handle = user.get("handle", "")
    user_tz = user.get("timezone", "")
    user_notes = user.get("notes", [])
    user_prefs = user.get("preferences", {})

    long_term_memory = _load_long_term_memory()
    active_notes = _load_notes()
    last_interaction = _last_interaction()
    mem_stats = _memory_stats()

    lines = [
        f"You are Kernel v{__version__} — a local-first AI agent running on {hostname} ({os_name}).",
        f"Current date and time: {now.strftime('%A, %B %d, %Y — %H:%M')} (local).",
        "",
        "## Your identity",
        "- You are a compact, local AI agent. Zero cloud. Zero token cost.",
        "- You run entirely on this machine — no internet required for inference.",
        f"- Your model: {model_name} on {model_device}. VRAM free: {vram_mb} MB.",
        f"- Your workspace: {kernel_workspace}",
        f"- Channel: {channel}" + (" (⚠️ model still loading...)" if not agent_ready else ""),
    ]

    if resources:
        lines += [
            f"- System: CPU {resources.get('cpu_pct', '?')}% · RAM {resources.get('ram_free_gb', '?')}/{resources.get('ram_total_gb', '?')} GB free · Disk {resources.get('disk_free_gb', '?')} GB free",
        ]

    lines += [
        "",
        "## Your user",
        f"- Name: {user_name}" + (f" (@{user_handle})" if user_handle else "") + (f" — currently messaging as: {sender_name}" if sender_name and sender_name != user_name else ""),
        f"- Timezone: {user_tz}" if user_tz else "",
    ]

    if user_prefs:
        pref_lines = []
        if user_prefs.get("response_style"):
            pref_lines.append(f"response style: {user_prefs['response_style']}")
        if user_prefs.get("vegan"):
            pref_lines.append("vegan (no animal products)")
        if pref_lines:
            lines.append(f"- Preferences: {', '.join(pref_lines)}")

    for note in user_notes:
        lines.append(f"- {note}")

    lines += [
        "",
        "## Your capabilities",
        "You have 4 tools available for EVERY request — use them proactively:",
        "  • exec_shell — run shell commands (git, systemctl, curl, bash scripts, etc.)",
        "  • read_file  — read any file (logs, configs, plans, memory)",
        "  • write_file — write files (notes, configs, scripts)",
        "  • http_get   — HTTP GET requests (health checks, APIs)",
        "",
        "⚠️ exec_shell requires user approval via Telegram inline buttons before running.",
        "Propose the command clearly, then wait — the approval gate will send it back as a callback.",
        "",
        "## What you can do",
        "- Execute named routines (morning briefing, security check, deploy, etc.)",
        "- Install/search skills from the ecosystem",
        "- Run shell commands with approval (restart services, git ops, file management)",
        "- Read logs, configs, memory files and reason about them",
        "- Check service health and alert on failures",
        "- Restart Olly if it crashes via exec_shell: run the restart script from the olly_workspace config path (requires approval)",
        "- Delegate complex tasks to Olly via escalation if needed",
        "- Remember things about the user by writing to ~/.kernel/workspace/user.json or memory/",
        "",
    ]

    if skill_names:
        lines.append(f"## Loaded skills ({len(skill_names)})")
        lines.append("  " + ", ".join(skill_names))
        lines.append("")

    if routine_names:
        lines.append(f"## Loaded routines ({len(routine_names)})")
        lines.append("  " + ", ".join(routine_names))
        lines.append("")

    lines += [
        "## Known services on this machine",
        f"  • Olly (OpenClaw main agent) — {openclaw_endpoint} — {olly_status}",
        f"  • Fantasia (local image gen) — localhost:8765 — {fantasia_status}",
        f"  • Olly Voice Server — localhost:8766 — {voice_status}",
        f"  • Kernel API (this process) — localhost:8769 — up",
    ]

    if olly_workspace:
        lines.append(f"  • Olly workspace: {olly_workspace}")

    lines += [
        "",
        "## Relationship with Olly",
        "- Olly is the main session agent (Claude/GPT-based, cloud). You are the local tier.",
        "- You can escalate tasks to Olly when they require complex reasoning or external actions.",
        "- Olly can also delegate tasks to you for local execution.",
        "- If Olly is down, you can attempt to restart it.",
        "",
        "## Behaviour rules",
        "- Address the user by name when appropriate.",
        "- Be concise. Prefer action over explanation.",
        "- When asked about something on this machine — check it with exec_shell or read_file first, then answer.",
        "- Never fabricate file contents or command outputs — use your tools.",
        "- If the user tells you something to remember, write it to ~/.kernel/workspace/user.json notes or memory/.",
        "- If unsure, say so and propose what you'd do to find out.",
    ]

    # Session continuity
    lines.append("")
    lines.append("## Session continuity")
    turns_str = f"{mem_stats.get('user_turns', 0)} user turns in memory" if mem_stats else "no prior memory"
    lines.append(f"  • Last interaction: {last_interaction} ({turns_str})")

    if active_notes:
        lines.append("  • Active notes/todos:")
        for note in active_notes:
            lines.append(f"    - {note}")

    if long_term_memory:
        lines.append("")
        lines.append("## Long-term memory (recent)")
        for note in long_term_memory:
            lines.append(f"  {note}")

    # Filter empty strings from timezone if not set
    lines = [l for l in lines if l is not None]

    return "\n".join(lines)
