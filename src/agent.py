"""
agent.py — Core Kernel agent loop.
Triage: handle locally or escalate to OpenClaw.
"""
import requests
import yaml
import os
from model import infer, infer_with_tools, vram_free_mb
from skills import load_all as load_skills, find as find_skill, find_semantic as find_skill_semantic, run as run_skill
from embedding_client import EmbeddingClient
from routines import load_all as load_routines, find as find_routine, run as run_routine
from replica import spawn, active as active_replicas, can_spawn
from tools import WORKSPACE as _DEFAULT_WORKSPACE
from context import build_system_prompt

_config = None
_skills = []
_routines = []
_embedding_client: EmbeddingClient | None = None


def init(config_path="config.yaml"):
    global _config, _skills, _routines
    with open(config_path) as f:
        _config = yaml.safe_load(f)
    # Env var overrides for Docker / bare-metal
    skills_dir = os.path.expanduser(os.environ.get("SKILLS_DIR") or _config.get("skills_dir", "./skills"))
    routines_dir = os.path.expanduser(os.environ.get("ROUTINES_DIR") or _config.get("routines_dir", "./routines"))
    _skills   = load_skills(skills_dir)
    _routines = load_routines(routines_dir)
    embedding_url = _config.get("embedding_server_url", "http://localhost:8770/embeddings")
    _embedding_client = EmbeddingClient(
        backend=_config.get("embedding_backend", "http"),
        url=embedding_url,
    )
    print(f"[agent] {len(_skills)} skills, {len(_routines)} routines loaded")


def triage(text: str, step_callback=None) -> str:
    """
    Classify and handle a user request.
    Returns the response string.
    """
    lower = text.lower().strip()

    # --- Slash commands (highest priority) ---
    if lower.startswith("/skills"):
        lines = [f"• {s['name']} — {s.get('description', '')}" for s in _skills]
        return "\n".join(lines) if lines else "No skills loaded."

    if lower.startswith("/routines"):
        lines = [f"• {r['name']} — {r.get('description', '')}" for r in _routines]
        return "\n".join(lines) if lines else "No routines loaded."

    if lower.startswith("/run "):
        name = text[5:].strip().lower()
        for r in _routines:
            if r["name"].lower() == name:
                print(f"[agent] /run routine: {r['name']}")
                return run_routine(r, infer, workspace=_DEFAULT_WORKSPACE)
        for s in _skills:
            if s["name"].lower() == name:
                print(f"[agent] /run skill: {s['name']}")
                return run_skill(s, text, infer)
        return f"No skill or routine named '{name}' found."

    if lower.startswith("/skill "):
        # Explicit skill invocation with fuzzy name matching
        query = text[7:].strip().lower()
        matched = None
        for s in _skills:
            sname = s["name"].lower()
            sdesc = s.get("description", "").lower()
            scmds = [c.lower().lstrip("/") for c in s.get("commands", [])]
            if query == sname or query in sdesc or query in scmds or sname.startswith(query):
                matched = s
                break
        if matched:
            print(f"[agent] /skill explicit: {matched['name']}")
            return run_skill(matched, text, infer)
        return f"No skill matching '{query}' found. Try /skills to list all."

    if lower.startswith("/status"):
        replicas = active_replicas()
        return (
            f"Kernel status:\n"
            f"  VRAM free: {vram_free_mb()} MB\n"
            f"  Active replicas: {len(replicas)}/{3}\n"
            f"  Can spawn: {can_spawn()}\n"
            f"  Skills: {len(_skills)}\n"
            f"  Routines: {len(_routines)}"
        )

    # --- Routine trigger ---
    for r in _routines:
        if r["name"].lower() in lower or f"run {r['name'].lower()}" in lower:
            print(f"[agent] Running routine: {r['name']}")
            return run_routine(r, infer, workspace=_DEFAULT_WORKSPACE)

    # --- Skill trigger (improved matching: name, description keywords, commands) ---
    for s in _skills:
        sname = s["name"].lower()
        sdesc_words = set(s.get("description", "").lower().split())
        scmds = [c.lower().lstrip("/") for c in s.get("commands", [])]
        # Match on skill name substring
        if sname in lower:
            print(f"[agent] Running skill (name match): {s['name']}")
            return run_skill(s, text, infer)
        # Match on any command trigger
        for cmd in scmds:
            if cmd and cmd in lower:
                print(f"[agent] Running skill (command match '{cmd}'): {s['name']}")
                return run_skill(s, text, infer)

    # --- Semantic skill fallback ---
    if _embedding_client is not None:
        sem_skill = find_skill_semantic(text, _skills, _embedding_client)
        if sem_skill:
            print(f"[agent] Running skill (semantic match): {sem_skill['name']}")
            return run_skill(sem_skill, text, infer)

    # --- Status / system queries ---
    if any(w in lower for w in ["status", "vram", "replicas", "health"]):
        replicas = active_replicas()
        return (
            f"Kernel status:\n"
            f"  VRAM free: {vram_free_mb()} MB\n"
            f"  Active replicas: {len(replicas)}/{3}\n"
            f"  Can spawn: {can_spawn()}\n"
            f"  Skills: {len(_skills)}\n"
            f"  Routines: {len(_routines)}"
        )

    # --- Tool-enabled inference (fallback for all free-form requests) ---
    # Always route through infer_with_tools so Gemma can use exec_shell,
    # read_file, write_file, http_get for any task — not just slash commands.
    from tools import TOOLS
    print(f"[agent] Routing to infer_with_tools: {text[:60]!r}")
    from model import vram_free_mb as _vram_free
    system_prompt = build_system_prompt(_config, _skills, _routines, vram_free_fn=_vram_free)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]
    return infer_with_tools(messages, TOOLS, step_callback=step_callback)


def escalate(text: str) -> str:
    """Delegate to OpenClaw main session."""
    endpoint = _config.get("api", {}).get("openclaw_endpoint", "http://localhost:18789")
    try:
        r = requests.post(f"{endpoint}/api/message", json={"message": text}, timeout=10)
        return r.json().get("reply", "Escalated to Olly.")
    except Exception as e:
        return f"Escalation failed: {e}"
