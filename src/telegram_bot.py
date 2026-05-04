"""
telegram_bot.py — Kernel's own Telegram bot.
Runs independently — no Olly in the loop.
Connects directly: Telegram → Gemma 4 local → Telegram

Usage: python src/telegram_bot.py
"""

import os
import json
import re
import subprocess
import requests
import threading
import time
import sys
from datetime import date
from pathlib import Path

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent))

BOT_TOKEN = os.environ.get("MICROCLAW_TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID = os.environ.get("MICROCLAW_TELEGRAM_CHAT_ID")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
CONFIG_PATH = str(Path(__file__).parent.parent / "config.yaml")
REPO_DIR = str(Path(__file__).parent.parent)

# GitHub update tracking
_latest_version: str = ""
# GitHub URLs now live in src/updater.py

# Lazy model load
_agent_ready = False

# Verbose mode — stream tool call steps to Telegram when ON
_verbose_mode = False

# In-process memory (list of {role, content} dicts)
_memory: list = []


def _call_api(method: str, path: str, body: dict = None):
    """Internal helper to call Kernel's own API."""
    try:
        url = f"http://localhost:8769{path}"
        if method == "GET":
            r = requests.get(url, timeout=5)
        elif method == "DELETE":
            r = requests.delete(url, timeout=5)
        elif method == "POST":
            r = requests.post(url, json=body, timeout=5)
        else:
            return None
        return r.json() if r.ok else None
    except Exception:
        return None


def send_message(chat_id: str, text: str, parse_mode: str = "Markdown"):
    try:
        requests.post(
            f"{API_BASE}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[bot] send error: {e}")


def send_buttons(chat_id: str, text: str, buttons: list):
    """Send a message with inline keyboard buttons."""
    try:
        requests.post(
            f"{API_BASE}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": buttons},
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[bot] send_buttons error: {e}")


def send_typing(chat_id: str):
    """Send typing indicator to show Kernel is processing."""
    try:
        requests.post(
            f"{API_BASE}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=3,
        )
    except Exception:
        pass


def download_file(file_id: str) -> "str | None":
    """Download a Telegram file by file_id. Returns local temp path or None."""
    import tempfile, os
    try:
        r = requests.get(f"{API_BASE}/getFile", params={"file_id": file_id}, timeout=10)
        file_path = r.json()["result"]["file_path"]
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        ext = os.path.splitext(file_path)[1] or ".bin"
        tmp = tempfile.mktemp(suffix=ext)
        data = requests.get(url, timeout=30)
        open(tmp, "wb").write(data.content)
        return tmp
    except Exception as e:
        print(f"[bot] download_file error: {e}")
        return None


def handle_callback(chat_id: str, data: str, message_id: int):
    """Handle inline button callbacks."""
    if data.startswith("install_"):
        # install_skill_name or install_routine_name
        parts = data.split("_", 2)
        item_type = parts[1] if len(parts) > 1 else None
        name = parts[2] if len(parts) > 2 else ""
        from bootstrap import install as eco_install
        result = eco_install(name, item_type)
        send_message(chat_id, result["message"])
        return
    elif data.startswith("routine_run_"):
        name = data[12:]
        handle_message(chat_id, f"/run {name}")
    elif data.startswith("routine_info_"):
        name = data[13:]
        _ensure_agent()
        import yaml

        with open(CONFIG_PATH) as _f:
            _cfg = yaml.safe_load(_f)
        from routines import load_all, find

        routines = load_all(
            str(os.path.expanduser(_cfg.get("routines_dir", str(Path(__file__).parent.parent / "routines"))))
        )
        r = find(name, routines)
        if r:
            trigger = r.get("trigger", {})
            t = (
                trigger.get("also", trigger.get("cron", "manual"))
                if isinstance(trigger, dict)
                else "manual"
            )
            send_message(
                chat_id,
                f"*{r['name']}*\n_{r['description']}_\nTrigger: `{t}`\n\nTap ▶ Run to execute.",
            )
    elif data.startswith("skill_info_"):
        name = data[11:]
        _ensure_agent()
        import yaml

        with open(CONFIG_PATH) as _f:
            _cfg = yaml.safe_load(_f)
        from skills import load_all, find

        skills = load_all(
            str(os.path.expanduser(_cfg.get("skills_dir", str(Path(__file__).parent.parent / "skills"))))
        )
        s = find(name, skills)
        if s:
            send_message(chat_id, f"*{s['name']}*\n_{s['description']}_")


    elif data.startswith("/"):
        # Slash command button — route through handle_message
        handle_message(chat_id, data)

def _search_collective_memory(query: str) -> str:
    """Run the collective memory search script and return results (or empty string)."""
    script = "~/.openclaw/workspace/collective-memory/scripts/search.py"
    try:
        result = subprocess.run(
            ["python3", script, query, "--top", "2"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _write_collective_memory(user_message: str, reply: str) -> None:
    """Write a new collective memory entry based on the conversation turn."""
    entries_dir = Path("~/.openclaw/workspace/collective-memory/entries")
    entries_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    # Infer topic from first 6 words of user message
    words = re.sub(r"[^a-zA-Z0-9\s]", "", user_message).split()[:6]
    topic = " ".join(words) if words else "general"
    slug = re.sub(r"\s+", "-", topic.lower())[:60]
    filename = entries_dir / f"{today}-{slug}.md"

    # Simple confidence heuristic: if reply contains hedging words → inferred
    hedging = re.search(
        r"\b(maybe|perhaps|might|could|unsure|unclear|I think|probably)\b", reply, re.I
    )
    confidence = "inferred" if hedging else "confirmed"

    # Bullet-point the reply lines (first 10 non-empty lines)
    lines = [l.strip() for l in reply.splitlines() if l.strip()][:10]
    bullets = "\n".join(f"- {l}" for l in lines)

    content = (
        f"---\n"
        f"date: {today}\n"
        f"agent: kernel\n"
        f"topic: {topic}\n"
        f"tags: [telegram, auto]\n"
        f"confidence: {confidence}\n"
        f"---\n"
        f"# {topic.title()}\n"
        f"{bullets}\n"
    )
    try:
        filename.write_text(content)
    except Exception as e:
        print(f"[bot] collective memory write error: {e}")


def handle_message(chat_id: str, text: str, sender_name: str = "", photo_file_id: str = "", voice_file_id: str = ""):
    global _agent_ready

    # Auth check
    if ALLOWED_CHAT_ID and str(chat_id) != str(ALLOWED_CHAT_ID):
        send_message(chat_id, "❌ Unauthorized.")
        return

    text = text.strip()

    # Send typing indicator immediately
    send_typing(chat_id)

    # Handle image attachments
    if photo_file_id and not text:
        text = "Describe this image."
    if photo_file_id:
        _ensure_agent()
        local_path = download_file(photo_file_id)
        if local_path:
            try:
                from model import infer_with_image
                reply = infer_with_image(local_path, text or "Describe this image.")
                os.unlink(local_path)
                send_message(chat_id, f"🦞 {reply}")
            except Exception as e:
                send_message(chat_id, f"🦞 Image error: {str(e)[:200]}")
        else:
            send_message(chat_id, "🦞 Could not download image.")
        return

    # Handle voice notes
    if voice_file_id:
        _ensure_agent()
        local_path = download_file(voice_file_id)
        if local_path:
            try:
                from model import infer_with_audio
                prompt = text if text else "Transcribe this audio and respond."
                reply = infer_with_audio(local_path, prompt)
                os.unlink(local_path)
                send_message(chat_id, f"🦞 {reply}")
            except Exception as e:
                send_message(chat_id, f"🦞 Audio error: {str(e)[:200]}")
        else:
            send_message(chat_id, "🦞 Could not download voice note.")
        return

    # Slash commands
    if text in ("/start", "/help"):
        from version import __version__
        loading_note = "\n⏳ _Model still loading — chat available in ~60s_" if not _agent_ready else ""
        intro = (
            f"🦞 *Kernel v{__version__}*\n"
            "Local AI agent · Gemma 4 · zero cloud\n"
            "Tap a command or just type anything to chat."
            f"{loading_note}"
        )
        buttons = [
            [{"text": "📋 /skills", "callback_data": "/skills"}, {"text": "⚙️ /routines", "callback_data": "/routines"}],
            [{"text": "📦 /packages", "callback_data": "/packages"}],
            [{"text": "📊 /status", "callback_data": "/status"}, {"text": "🔇 /verbose", "callback_data": "/verbose"}],
            [{"text": "🔍 /search ...", "callback_data": "/search "}, {"text": "📦 /install ...", "callback_data": "/install "}],
            [{"text": "🤖 /replica list", "callback_data": "/replica list"}, {"text": "➕ /replica spawn", "callback_data": "/replica spawn"}],
            [{"text": "👥 /replica clone", "callback_data": "/replica clone"}, {"text": "⏹ /replica stop", "callback_data": "/replica stop "}],
            [{"text": "🔄 /update", "callback_data": "/update"}, {"text": "🔁 /restart", "callback_data": "/restart"}],
            [{"text": "⏪ /rollback", "callback_data": "/rollback"}],
        ]
        send_buttons(chat_id, intro, buttons)
        return

    if text == "/skills":
        _ensure_agent()
        import yaml

        with open(CONFIG_PATH) as _f:
            _cfg = yaml.safe_load(_f)
        from skills import load_all

        skills = load_all(
            str(os.path.expanduser(_cfg.get("skills_dir", str(Path(__file__).parent.parent / "skills"))))
        )
        if not skills:
            send_message(chat_id, "🔧 No skills loaded.")
        else:
            # Send as inline buttons (2 per row)
            buttons = []
            row = []
            for s in skills:
                row.append(
                    {"text": s["name"], "callback_data": f"skill_info_{s['name']}"}
                )
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            send_buttons(
                chat_id, f"🔧 *Skills ({len(skills)}) — tap to learn more:*", buttons
            )
        return

    if text == "/routines":
        _ensure_agent()
        import yaml

        with open(CONFIG_PATH) as _f:
            _cfg = yaml.safe_load(_f)
        from routines import load_all

        routines = load_all(
            str(os.path.expanduser(_cfg.get("routines_dir", str(Path(__file__).parent.parent / "routines"))))
        )
        if not routines:
            send_message(chat_id, "⚙️ No routines loaded.")
        else:
            # Send as inline buttons — one per row with Run button
            buttons = []
            for r in routines:
                buttons.append(
                    [
                        {
                            "text": f"⚙️ {r['name']}",
                            "callback_data": f"routine_info_{r['name']}",
                        },
                        {"text": "▶ Run", "callback_data": f"routine_run_{r['name']}"},
                    ]
                )
            send_buttons(chat_id, f"⚙️ *Routines ({len(routines)}):*", buttons)
        return

    if text == "/packages":
        from bootstrap import ECOSYSTEM_ROOT
        import yaml
        skills_list = []
        routines_list = []
        for tier_dir in sorted(ECOSYSTEM_ROOT.iterdir()):
            manifest = tier_dir / "manifest.yaml"
            if not manifest.exists():
                continue
            with open(manifest) as f:
                data = yaml.safe_load(f) or {}
            tier = tier_dir.name
            for s in data.get("skills", []):
                skills_list.append((s["name"], s.get("description", ""), tier))
            for r in data.get("routines", []):
                routines_list.append((r["name"], r.get("description", ""), tier))

        lines_out = [f"📦 *Packages — {len(skills_list)} skills · {len(routines_list)} routines*\n"]
        lines_out.append("\n*Skills:*")
        for name, desc, tier in skills_list:
            lines_out.append(f"  • `{name}` — {desc} _[{tier}]_")
        lines_out.append("\n*Routines:*")
        for name, desc, tier in routines_list:
            lines_out.append(f"  • `{name}` — {desc} _[{tier}]_")

        buttons = []
        row = []
        for name, desc, tier in skills_list:
            row.append({"text": f"📥 {name}", "callback_data": f"install_skill_{name}"})
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        row = []
        for name, desc, tier in routines_list:
            row.append({"text": f"📥 {name}", "callback_data": f"install_routine_{name}"})
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        send_buttons(chat_id, "\n".join(lines_out), buttons)
        return

    if text == "/status":
        import torch
        from version import __version__

        free_mb = 0
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            free_mb = free // (1024 * 1024)
        update_note = f"\n🆕 Update available: {_latest_version}" if _latest_version and _latest_version != __version__ else ""
        send_message(
            chat_id,
            (
                f"🦞 *Kernel Status*\n"
                f"Version: v{__version__}{update_note}\n"
                f"Model: Gemma 4 E2B-it\n"
                f"VRAM free: {free_mb}MB\n"
                f"Ready: {'✅' if _agent_ready else '⏳ loading on first message'}"
            ),
        )
        return

    if text == "/restart":
        _handle_restart(chat_id)
        return

    if text == "/update":
        _handle_update(chat_id)
        return

    if text == "/rollback":
        _handle_rollback(chat_id)
        return

    if text.startswith("/private_repo"):
        arg = text[13:].strip()
        import yaml
        cfg = yaml.safe_load(open(CONFIG_PATH))
        if not arg or arg == "show":
            current = cfg.get("ecosystem", {}).get("private", "(not set)")
            send_message(chat_id, f"🔒 Private ecosystem repo: `{current}`\n\nTo change: `/private_repo owner/repo`")
            return
        # Validate format
        if "/" not in arg or len(arg.split("/")) != 2:
            send_message(chat_id, "❌ Invalid format. Use: `/private_repo owner/repo`\nExample: `/private_repo myorg/my-kernel-skills`")
            return
        # Save to config
        if "ecosystem" not in cfg:
            cfg["ecosystem"] = {}
        cfg["ecosystem"]["private"] = arg
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
        send_message(chat_id, f"✅ Private repo set to `{arg}`\nRe-bootstrapping ecosystem...")
        # Trigger re-bootstrap in background
        import threading
        def _rebootstrap():
            try:
                from bootstrap import bootstrap
                result = bootstrap(cfg)
                send_message(chat_id, f"✅ Bootstrap complete. Skills/routines updated.")
            except Exception as e:
                send_message(chat_id, f"⚠️ Bootstrap error: {str(e)[:200]}")
        threading.Thread(target=_rebootstrap, daemon=True).start()
        return

    if text.startswith("/clone"):
        parts = text[6:].strip().split(None, 1)
        if not parts:
            send_message(chat_id, "Usage: /clone <path> [name]\nExample: /clone ~/.openclaw/workspace/skills/mental-map")
            return
        src_path = parts[0]
        name = parts[1] if len(parts) > 1 else None
        from bootstrap import clone_from_agent
        result = clone_from_agent(src_path, name)
        send_message(chat_id, result["message"])
        return

    if text.startswith("/search"):
        query = text[7:].strip()
        if not query:
            send_message(chat_id, "Usage: /search <query>\nExample: /search tracker")
            return
        from bootstrap import search as eco_search
        results = eco_search(query)
        if not results:
            send_message(chat_id, f"🔍 No results for '{query}'.\nTry /search with a different term.")
            return
        lines = [f"🔍 *Results for '{query}':*\n"]
        for r in results[:10]:
            icon = "🔧" if r["type"] == "skill" else "⚙️"
            lines.append(f"{icon} *{r['name']}* ({r['source']})\n   {r['description'] or 'No description'}")
        buttons = [[{"text": f"📥 Install {r['name']}", "callback_data": f"install_{r['type']}_{r['name']}"}] for r in results[:5]]
        send_buttons(chat_id, "\n".join(lines), buttons)
        return

    if text.startswith("/install"):
        parts = text[8:].strip().split()
        if not parts:
            send_message(chat_id, "Usage: /install <name>\nExample: /install open-workspace-tracker")
            return
        name = parts[0]
        item_type = parts[1] if len(parts) > 1 else None
        from bootstrap import install as eco_install
        result = eco_install(name, item_type)
        send_message(chat_id, result["message"])
        return

    if text == "/verbose":
        global _verbose_mode
        _verbose_mode = not _verbose_mode
        if _verbose_mode:
            send_message(chat_id, "🔍 Verbose mode ON — I'll show my reasoning.")
        else:
            send_message(chat_id, "🔇 Verbose mode OFF.")
        return

    if text.startswith("/replica"):
        parts = text.split(None, 2)
        sub = parts[1] if len(parts) > 1 else "list"

        if sub == "clone":
            if len(parts) > 2:
                # /replica clone <agent_name> — spawn from agents dir
                agent_name = parts[2].strip().lower().replace('.md', '')
                agents_dir = Path.home() / '.openclaw' / 'workspace-client' / 'agents'
                brief_path = agents_dir / f"{agent_name}.md"
                if not brief_path.exists():
                    available = [f.stem for f in agents_dir.glob('*.md')] if agents_dir.exists() else []
                    send_message(chat_id, f"❌ Agent `{agent_name}` not found.\nAvailable: {', '.join(available) or 'none'}")
                else:
                    result = _call_api("POST", "/replica/named", {
                        "name": agent_name,
                        "role": "custom",
                        "brief_path": str(brief_path)
                    })
                    if result and result.get("status") == "spawned":
                        send_message(chat_id, f"✅ Replica `{agent_name}` spawned with brief.\nChat: `/replica msg {agent_name} <message>`")
                    else:
                        reason = result.get("reason", "unknown") if result else "unreachable"
                        send_message(chat_id, f"❌ Could not spawn: {reason}")
            else:
                # Show dynamic list of available agents as buttons
                agents_dir = Path.home() / '.openclaw' / 'workspace-client' / 'agents'
                agents = sorted([f.stem for f in agents_dir.glob('*.md')]) if agents_dir.exists() else []
                if not agents:
                    send_message(chat_id, "No agents found in workspace-client/agents/")
                else:
                    buttons = [[{"text": f"🤖 {a}", "callback_data": f"/replica clone {a}"}] for a in agents]
                    send_buttons(chat_id, "*Clone an agent replica:*\nSelect an agent to spawn with their brief loaded:", buttons)

        elif sub == "list":
            active = _call_api("GET", "/replica/active") or []
            if not active:
                send_message(chat_id, "No active replicas.")
            else:
                lines = ["*Active replicas:*"]
                for r in active:
                    status = "💬 persistent" if r.get("persistent") else ("✅ done" if r.get("done") else "⚙️ running")
                    lines.append(f"• `{r['name']}` ({r['role']}) — {status}")
                send_message(chat_id, "\n".join(lines))

        elif sub == "spawn":
            if len(parts) > 2:
                # /replica spawn <name> [prompt]
                spawn_parts = parts[2].split(None, 1)
                name = spawn_parts[0]
                prompt = spawn_parts[1] if len(spawn_parts) > 1 else None
                body = {"name": name}
                if prompt:
                    body["custom_prompt"] = prompt
                result = _call_api("POST", "/replica/named", body)
                if result and result.get("status") == "spawned":
                    send_message(chat_id, f"✅ Replica `{name}` spawned and ready.\nSend messages to it with:\n`/replica msg {name} <your message>`")
                else:
                    reason = result.get("reason", "unknown error") if result else "unreachable"
                    send_message(chat_id, f"❌ Could not spawn replica: {reason}")
            else:
                send_message(chat_id, "Usage:\n`/replica spawn <name>` — spawn with default prompt\n`/replica spawn <name> <custom prompt>` — spawn with custom role\n\nExample:\n`/replica spawn analyst You are a data analyst.`")

        elif sub == "msg" and len(parts) > 2:
            msg_parts = parts[2].split(None, 1)
            name = msg_parts[0]
            user_msg = msg_parts[1] if len(msg_parts) > 1 else ""
            if not user_msg:
                send_message(chat_id, "Usage: `/replica msg <name> <message>`")
            else:
                result = _call_api("POST", f"/replica/{name}/message", {"message": user_msg})
                if result and "reply" in result:
                    send_message(chat_id, f"🤖 *{name}:* {result['reply']}")
                else:
                    send_message(chat_id, f"❌ Replica `{name}` not found or error.")

        elif sub == "stop" and len(parts) > 2:
            name = parts[2].strip()
            result = _call_api("DELETE", f"/replica/{name}")
            if result and result.get("status") == "stopped":
                send_message(chat_id, f"✅ Replica `{name}` stopped.")
            else:
                send_message(chat_id, f"❌ Could not stop `{name}`.")

        else:
            send_message(chat_id, "Usage:\n`/replica list` — show active replicas\n`/replica clone` — spawn from available agents (dynamic list)\n`/replica clone <name>` — spawn a specific agent\n`/replica spawn <name> [prompt]` — spawn with custom prompt\n`/replica msg <name> <message>` — chat with a replica\n`/replica stop <name>` — stop a named replica")
        return

    if text.startswith("/run "):
        run_arg = text[5:].strip()
        # Split into name + optional input (e.g. "/run olly-recovery check status")
        parts = run_arg.split(" ", 1)
        run_name = parts[0]
        run_input = parts[1] if len(parts) > 1 else ""

        _ensure_agent()
        import yaml
        import os as _os

        with open(CONFIG_PATH) as _f:
            _cfg = yaml.safe_load(_f)
        from routines import load_all as load_routines, find as find_routine, run as run_routine
        from skills import load_all as load_skills, find as find_skill, run as run_skill
        from model import infer, infer_with_tools, _model
        from tools import TOOLS

        # Try routine first
        routines_dir = str(os.path.expanduser(_os.environ.get("ROUTINES_DIR") or _cfg.get("routines_dir", str(Path(__file__).parent.parent / "routines"))))
        routines = load_routines(routines_dir)
        r = find_routine(run_name, routines)
        if r:
            send_message(chat_id, f"⚙️ Running routine `{run_name}`...")
            result = run_routine(
                r,
                infer,
                workspace=_cfg.get("workspace", "~/.openclaw/workspace"),
            )
            send_message(chat_id, f"✅ `{run_name}` complete:\n\n{result[:3800]}")
            return

        # Try skill
        skills_dir = str(os.path.expanduser(_os.environ.get("SKILLS_DIR") or _cfg.get("skills_dir", str(Path(__file__).parent.parent / "skills"))))
        skills = load_skills(skills_dir)
        s = find_skill(run_name, skills)
        if s:
            send_message(chat_id, f"🔧 Running skill `{run_name}`...")
            user_input = run_input or f"Execute the {run_name} skill."
            result = run_skill(s, user_input, infer)
            send_message(chat_id, f"✅ `{run_name}` complete:\n\n{result[:3800]}")
            return

        send_message(chat_id, f"❌ No routine or skill named `{run_name}` found.\nTry /routines or /skills to see available options.")
        return

    # Regular chat — route to agent
    # For slash commands that weren't handled above, try agent.triage() first
    # so skills with exec dispatch (e.g. /markdown) run deterministically.
    if text.startswith("/"):
        _ensure_agent()
        import agent as _a
        result = _a.triage(text)
        if result:
            send_message(chat_id, f"🦞 {result[:3800]}")
            return

    if not _agent_ready:
        send_message(chat_id, "⏳ Loading model (~60s)...")

    _ensure_agent()
    from model import infer
    import memory as _memory_mod

    # Build rich system prompt with live context
    import agent as _agent_mod_ctx
    import yaml as _yaml_ctx
    import os as _os_ctx
    from context import build_system_prompt as _build_prompt
    from skills import load_all as _load_skills
    from routines import load_all as _load_routines
    from model import vram_free_mb as _vram_free
    _cfg_path = str(Path(__file__).parent.parent / "config.yaml")
    _cfg = _yaml_ctx.safe_load(open(_cfg_path))
    _skills = _load_skills(_os_ctx.path.expanduser(_cfg.get("skills_dir", "./skills")))
    _routines = _load_routines(_os_ctx.path.expanduser(_cfg.get("routines_dir", "./routines")))
    system_prompt = _build_prompt(
        _cfg, _skills, _routines,
        vram_free_fn=_vram_free,
        channel="telegram",
        sender_name=sender_name,
        agent_ready=_agent_ready,
    )

    # Prepend relevant collective memory results if found
    memory_results = _search_collective_memory(text)
    if memory_results:
        system_prompt = f"Relevant memory:\n{memory_results}\n\n{system_prompt}"

    try:
        if _verbose_mode:
            # Verbose path — route through agent.triage() with step callback
            import agent as _agent_mod
            step_num = [0]

            def _step_cb(n, tool_name, args, result):
                step_num[0] = n
                args_str = str(args)[:100]
                result_str = str(result)[:200]
                send_message(
                    chat_id,
                    f"🔍 *Step {n}* — `{tool_name}`\n▸ `{args_str}`\n↳ {result_str}",
                )

            reply = _agent_mod.triage(text, step_callback=_step_cb)
            if step_num[0] > 0:
                send_message(
                    chat_id,
                    f"✅ *Done* ({step_num[0]} step{'s' if step_num[0] != 1 else ''})",
                )
        else:
            # Normal path — plain infer with memory context
            history = _memory_mod.load()
            messages = (
                [{"role": "system", "content": system_prompt}]
                + history
                + [{"role": "user", "content": text}]
            )
            reply = infer(messages, max_new_tokens=512)
            # Persist user + assistant turn
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": reply})
            _memory_mod.save(history)
            # Write collective memory entry for substantive replies
            if len(reply) > 100:
                _write_collective_memory(text, reply)

        send_message(chat_id, f"🦞 {reply}")
    except Exception as e:
        print(f"[bot] ERROR in infer: {e}", flush=True)
        send_message(chat_id, f"🦞 Error: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# Self-update helpers — delegated to src/updater.py
# ---------------------------------------------------------------------------

from updater import (
    get_current_version as _get_current_version,
    fetch_latest_version as _fetch_latest_version,
    check_update_available as _check_update_available,
    do_update as _do_update,
)


def _restart_via_start_sh():
    """Launch start.sh and exit so systemd/start.sh relaunches the process."""
    subprocess.Popen(
        ["bash", os.path.join(REPO_DIR, "start.sh")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import threading
    threading.Timer(2.0, lambda: os._exit(0)).start()


def _check_for_update(notify_chat_id: str = ""):
    """Check GitHub for a newer release. Optionally notify via Telegram."""
    global _latest_version
    current = _get_current_version()
    latest = _fetch_latest_version()
    if not latest:
        return
    _latest_version = latest
    if latest != current and notify_chat_id:
        send_message(
            notify_chat_id,
            f"🆕 Kernel v{latest} available (current: v{current}). /update to apply.",
        )
        print(f"[bot] Update available: {latest} (current: {current})")


def _update_check_loop(chat_id: str):
    """Background thread: check for updates every 6 hours."""
    time.sleep(30)
    while True:
        _check_for_update(notify_chat_id=chat_id)
        time.sleep(6 * 3600)


def _handle_restart(chat_id: str):
    """Restart the process without pulling. Called when user types /restart."""
    send_message(chat_id, "🔄 Restarting Kernel... back in ~30s.")
    try:
        _restart_via_start_sh()
    except Exception as e:
        send_message(chat_id, f"❌ Restart failed: {str(e)[:200]}")


def _handle_update(chat_id: str):
    """Execute git pull + restart via shared updater. Called when user types /update."""
    _do_update(
        notify=lambda msg: send_message(chat_id, msg),
        restart_fn=_restart_via_start_sh,
    )


def _handle_rollback(chat_id: str):
    """Revert one commit + restart. Called when user types /rollback."""
    try:
        result = subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"[bot] git reset: {result.stdout} {result.stderr}")
        send_message(chat_id, "⏪ Rolled back to previous version. Restarting...")
        _restart_via_start_sh()
    except Exception as e:
        send_message(chat_id, f"❌ Rollback failed: {str(e)[:200]}")


def _ensure_agent():
    global _agent_ready
    if not _agent_ready:
        import model as _m

        # Check if model server is running OR model is loaded in-process
        from model_client import is_server_running as _is_srv
        if _m._model is None and not _is_srv():
            _m.load(CONFIG_PATH)
        import agent

        agent.init(CONFIG_PATH)
        _agent_ready = True


def start_bot_thread():
    """Start the Telegram polling loop as a daemon thread.
    Assumes the model is already loaded (called from API startup event).
    """
    global _agent_ready
    if not BOT_TOKEN:
        print("[bot] MICROCLAW_TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        return
    # Mark agent ready so _ensure_agent() won't try to reload the model
    import model as _m
    import agent as _agent_mod

    from model_client import is_server_running as _is_srv
    if _m._model is not None or _is_srv():
        _agent_ready = True
    t = threading.Thread(target=poll, daemon=True, name="kernel-telegram-bot")
    t.start()
    print(
        f"[bot] Telegram bot thread started (allowed chat: {ALLOWED_CHAT_ID or 'all'})"
    )
    # Start background update checker
    if ALLOWED_CHAT_ID:
        u = threading.Thread(
            target=_update_check_loop,
            args=(ALLOWED_CHAT_ID,),
            daemon=True,
            name="kernel-update-checker",
        )
        u.start()
        print("[bot] Update checker started (6h interval)")


OFFSET_FILE = "/tmp/kernel_telegram_offset"


def poll():
    """Long-poll Telegram for updates."""
    # Restore offset from last run so we don't replay already-seen updates
    offset = None
    try:
        with open(OFFSET_FILE) as _f:
            offset = int(_f.read().strip())
        print(f"[bot] Restored Telegram offset: {offset}")
    except Exception:
        pass
    print(f"[bot] Kernel Telegram bot starting...")

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset

            resp = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=35)
            data = resp.json()

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                # Persist offset so restarts don't replay seen updates
                try:
                    with open(OFFSET_FILE, "w") as _f:
                        _f.write(str(offset))
                except Exception:
                    pass
                # Handle callback queries (button taps)
                cb = update.get("callback_query", {})
                if cb:
                    cb_chat = cb.get("message", {}).get("chat", {}).get("id")
                    cb_data = cb.get("data", "")
                    cb_msg_id = cb.get("message", {}).get("message_id")
                    if cb_chat and cb_data:
                        threading.Thread(
                            target=handle_callback,
                            args=(str(cb_chat), cb_data, cb_msg_id),
                            daemon=True,
                        ).start()
                    # Answer callback to remove loading state
                    try:
                        requests.post(
                            f"{API_BASE}/answerCallbackQuery",
                            json={"callback_query_id": cb.get("id", "")},
                            timeout=5,
                        )
                    except:
                        pass
                    continue

                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                sender_name = msg.get("from", {}).get("first_name", "") or msg.get("from", {}).get("username", "")
                # Extract media
                photos = msg.get("photo", [])
                photo_file_id = photos[-1]["file_id"] if photos else ""  # largest size
                voice = msg.get("voice", {}) or msg.get("audio", {})
                voice_file_id = voice.get("file_id", "")
                caption = msg.get("caption", "")
                # Use caption as text for media messages
                effective_text = text or caption

                if chat_id and (effective_text or photo_file_id or voice_file_id):
                    # Send typing indicator immediately (before thread starts)
                    send_typing(str(chat_id))
                    # Handle in thread so polling doesn't block
                    threading.Thread(
                        target=handle_message,
                        args=(str(chat_id), effective_text, sender_name, photo_file_id, voice_file_id),
                        daemon=True
                    ).start()

        except KeyboardInterrupt:
            print("[bot] Stopped.")
            break
        except Exception as e:
            print(f"[bot] Poll error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: MICROCLAW_TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    # Load .env if not already loaded
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    print("[bot] Starting Kernel Telegram bot...")
    print(f"[bot] Allowed chat: {ALLOWED_CHAT_ID or 'all'}")

    # Load persisted memory on startup
    import memory as _memory_startup

    _mem = _memory_startup.load()
    print(f"[bot] Memory loaded: {len(_mem)} message(s) from previous sessions")

    print("[bot] Checking model availability...")
    import model as _model_module
    from model_client import is_server_running as _is_srv

    if _is_srv():
        print("[bot] Model server detected — skipping in-process load")
    else:
        _model_module.load(CONFIG_PATH)
    print("[bot] Model ready, starting polling...")
    poll()
