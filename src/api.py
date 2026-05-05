"""
api.py — FastAPI server. Local + mesh endpoints.
Port 8769 by default.
"""
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import yaml, agent, replica as rep, model as mdl
import bootstrap as _bootstrap
import os, json, time, uuid

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Kernel", version="0.2.0")

_cfg = {}

# ── Interaction logger ──────────────────────────────────────────────────────
_LOG_DIR  = os.path.expanduser("~/.kernel/workspace/logs")
_LOG_FILE = os.path.join(_LOG_DIR, "kernel_calls.jsonl")
os.makedirs(_LOG_DIR, exist_ok=True)

def _log_interaction(prompt: str, reply: str, source: str = "api", meta: dict | None = None) -> None:
    """Append one JSONL record to the interaction log (non-blocking best-effort)."""
    try:
        record = {
            "id":        str(uuid.uuid4()),
            "timestamp": time.time(),
            "source":    source,
            "prompt":    prompt,
            "reply":     reply,
            "meta":      meta or {},
        }
        with open(_LOG_FILE, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:
        print(f"[logger] warning: could not write interaction log: {exc}")
# ────────────────────────────────────────────────────────────────────────────


class MessageIn(BaseModel):
    message: str

class TaskIn(BaseModel):
    role: str
    task: str

class NamedReplicaIn(BaseModel):
    name: str
    role: str = "custom"
    brief_path: Optional[str] = None
    custom_prompt: Optional[str] = None
    workspace: Optional[str] = None

class ReplicaMessageIn(BaseModel):
    message: str


@app.on_event("startup")
async def startup():
    global _cfg
    with open(os.path.join(_BASE, "config.yaml")) as f:
        _cfg = yaml.safe_load(f)
    # Bootstrap skills and routines from ecosystem repos
    counts = _bootstrap.bootstrap(_cfg)
    if counts["skills"] or counts["routines"]:
        print(f"[bootstrap] Added {counts['skills']} skills, {counts['routines']} routines from ecosystem")

    _config_path = os.path.join(_BASE, "config.yaml")
    mdl.load(_config_path)
    agent.init(_config_path)
    print(f"[api] Kernel ready on :{_cfg['api']['port']}")
    # Start Think-at-Rest if enabled
    from thought_engine import ThinkAtRest
    _think = ThinkAtRest(config=_cfg)
    if _cfg.get("thinking", {}).get("enabled", False):
        _think.start()
    app.state.think_at_rest = _think
    # Start Telegram bot in-process (model already loaded above)
    import telegram_bot
    telegram_bot.start_bot_thread()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "vram_free_mb": mdl.vram_free_mb(),
        "active_replicas": len(rep.active()),
        "skills": len(agent._skills),
        "routines": len(agent._routines),
    }


@app.post("/message")
def message(body: MessageIn):
    """Main entry point — triage and respond."""
    text = body.message.strip()
    if not text:
        return {"reply": ""}

    # Built-in slash commands go through telegram_bot handler (captures send_message calls)
    _BUILTIN_COMMANDS = {
        "/start", "/help", "/skills", "/routines", "/run", "/skill",
        "/status", "/verbose", "/packages", "/search", "/install",
        "/clone", "/private_repo", "/update", "/restart", "/rollback",
        "/replica", "/workspaces", "/system", "/version",
    }
    cmd_word = text.split()[0].lower() if text.startswith("/") else ""
    if cmd_word and cmd_word in _BUILTIN_COMMANDS:
        import telegram_bot as _tb
        _replies = []
        _orig_send = _tb.send_message
        def _capture(chat_id, msg, **kwargs):
            _replies.append(msg)
        _tb.send_message = _capture
        try:
            _tb.handle_message(_tb.ALLOWED_CHAT_ID or "api", text)
        finally:
            _tb.send_message = _orig_send
        result = "\n".join(_replies) if _replies else ""
        _log_interaction(text, result, source="api-slash")
        return {"reply": result}

    # Everything else (free text + unknown slash commands) through agent.triage()
    # This includes exec-backed skill commands like /markdown, /anonymize
    reply = agent.triage(text)
    _log_interaction(text, reply, source="api")
    return {"reply": reply}


@app.get("/skills")
def list_skills():
    return [{"name": s["name"], "description": s["description"]} for s in agent._skills]


@app.get("/routines")
def list_routines():
    return [{"name": r["name"], "description": r["description"], "trigger": r["trigger"]} for r in agent._routines]


@app.post("/replica/spawn")
def spawn_replica(body: TaskIn):
    """Spawn a specialist replica if VRAM allows."""
    r = rep.spawn(body.role, body.task)
    if r is None:
        return {"status": "rejected", "reason": "VRAM limit or replica cap reached"}
    return {"status": "spawned", "role": r.role, "name": r.name}


@app.get("/replica/active")
def active_replicas():
    return [{"name": r.name, "role": r.role, "task": r.task[:80], "done": r.done, "persistent": r.persistent} for r in rep.active()]


@app.post("/replica/named")
def spawn_named_replica(body: NamedReplicaIn):
    """Spawn a named persistent replica with optional brief injection."""
    r = rep.spawn_named(
        name=body.name,
        role=body.role,
        brief_path=body.brief_path,
        custom_prompt=body.custom_prompt,
        workspace=body.workspace,
    )
    if r is None:
        return {"status": "rejected", "reason": "VRAM limit or replica cap reached"}
    return {"status": "spawned", "name": r.name, "role": r.role, "persistent": r.persistent}


@app.post("/replica/{name}/message")
def message_replica(name: str, body: ReplicaMessageIn):
    """Send a message to a named persistent replica."""
    r = rep.get(name)
    if r is None:
        return JSONResponse({"error": f"Replica '{name}' not found"}, status_code=404)
    if not r.persistent:
        return JSONResponse({"error": "Replica is not in persistent mode"}, status_code=400)
    reply = r.message(body.message)
    return {"name": name, "reply": reply}


@app.delete("/replica/{name}")
def stop_replica(name: str):
    """Stop and remove a named replica."""
    if rep.stop(name):
        return {"status": "stopped", "name": name}
    return JSONResponse({"error": f"Replica '{name}' not found"}, status_code=404)


@app.get("/replica/{name}/status")
def replica_status(name: str):
    """Get status of a named replica."""
    r = rep.get(name)
    if r is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {
        "name": r.name,
        "role": r.role,
        "persistent": r.persistent,
        "done": r.done,
        "brief_path": r.brief_path,
        "history_turns": len(r.history),
    }


@app.get("/system")
def system_info():
    return {
        "vram_free_mb": mdl.vram_free_mb(),
        "can_spawn": rep.can_spawn(),
        "max_replicas": rep.MAX_REPLICAS,
        "context_budget_mb": rep.CONTEXT_BUDGET_MB,
    }


@app.get("/version")
def get_version():
    from version import __version__
    return {"version": __version__}


@app.get("/workspaces")
def list_workspaces_endpoint():
    from workspaces import list_workspaces as _list
    return _list()


@app.get("/thoughts")
def get_thoughts():
    """Return last 20 thoughts from today's journal."""
    from thought_journal import ThoughtJournal
    journal = ThoughtJournal(journal_dir=_cfg.get("thinking", {}).get("journal_dir"))
    thoughts = journal.read_today()
    return thoughts[-20:] if len(thoughts) > 20 else thoughts


@app.get("/thoughts/today")
def get_thoughts_today():
    """Return today's full journal as markdown text."""
    import datetime
    from thought_journal import ThoughtJournal
    journal = ThoughtJournal(journal_dir=_cfg.get("thinking", {}).get("journal_dir"))
    journal_dir = journal.journal_dir
    path = os.path.join(journal_dir, datetime.date.today().strftime("%Y-%m-%d") + ".md")
    if not os.path.exists(path):
        return {"date": str(datetime.date.today()), "content": "", "entries": 0}
    with open(path, encoding="utf-8") as f:
        content = f.read()
    return {"date": str(datetime.date.today()), "content": content, "entries": content.count("## ")}
