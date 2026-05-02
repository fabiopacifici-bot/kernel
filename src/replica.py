"""
replica.py — Spawn specialist sub-agents within VRAM limits.
Shared model weights. Isolated context and conversation history per replica.
"""
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from model import vram_free_mb, infer

CONTEXT_BUDGET_MB = 512
MAX_REPLICAS = 4  # increased from 3 to support named meeting agents

_replicas: dict[str, "Replica"] = {}  # name → Replica (was a list)
_lock = threading.Lock()

BUILTIN_ROLES = {
    "researcher": "You are a research specialist. Gather information, search for facts, and synthesise findings clearly.",
    "coder":      "You are a coding specialist. Write clean, working code based on the specification provided.",
    "reviewer":   "You are a code reviewer. Validate output for correctness, security, and quality.",
    "reporter":   "You are a reporting specialist. Summarise results concisely and deliver them clearly.",
}

# Keep backward-compat alias
ROLES = BUILTIN_ROLES


@dataclass
class Replica:
    name: str                        # unique identifier e.g. "lawy", "marty"
    role: str                        # role key or "custom"
    system_prompt: str               # full system prompt (built from role + brief)
    task: str = ""                   # initial task (empty for persistent replicas)
    persistent: bool = False         # if True, stays alive after first response
    brief_path: Optional[str] = None # path to brief file (loaded into system prompt)
    workspace: Optional[str] = None  # scoped workspace path (read-only)
    result: str = ""
    done: bool = False
    history: list = field(default_factory=list)  # conversation history for persistent mode
    _thread: Optional[threading.Thread] = field(default=None, repr=False)

    def start(self, callback=None):
        """Start replica. For persistent replicas, just mark as ready. For task replicas, run the task."""
        if self.persistent:
            self.done = False  # persistent = never done until explicitly stopped
            return
        self._thread = threading.Thread(target=self._run_task, daemon=True)
        self._thread.start()

    def _run_task(self):
        """Fire-and-forget task execution (backward compat)."""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user",   "content": self.task},
        ]
        self.result = infer(messages, max_new_tokens=1024)
        self.done = True
        with _lock:
            _replicas.pop(self.name, None)

    def message(self, user_text: str) -> str:
        """Send a message to a persistent replica and get a response."""
        if not self.persistent:
            return "This replica is not in persistent mode."
        self.history.append({"role": "user", "content": user_text})
        messages = [{"role": "system", "content": self.system_prompt}] + self.history[-20:]
        reply = infer(messages, max_new_tokens=512)
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def stop(self):
        """Shut down a persistent replica."""
        with _lock:
            _replicas.pop(self.name, None)
        self.done = True


def _build_system_prompt(role: str, brief_path: Optional[str] = None, custom_prompt: Optional[str] = None) -> str:
    """Build system prompt from role + optional brief file."""
    base = custom_prompt or BUILTIN_ROLES.get(role, f"You are a {role} specialist.")
    if brief_path:
        try:
            path = Path(brief_path).expanduser()
            if path.exists():
                brief_content = path.read_text(encoding="utf-8")
                base = f"{base}\n\n## Your Brief\n\n{brief_content}"
        except Exception:
            pass
    return base


def can_spawn() -> bool:
    return len(_replicas) < MAX_REPLICAS and vram_free_mb() > CONTEXT_BUDGET_MB


def spawn(role: str, task: str, name: Optional[str] = None) -> Optional["Replica"]:
    """Backward-compat: spawn a fire-and-forget task replica."""
    _name = name or f"{role}_{len(_replicas)}"
    with _lock:
        if not can_spawn():
            return None
        if _name in _replicas:
            return _replicas[_name]  # already running
        system = _build_system_prompt(role)
        r = Replica(name=_name, role=role, system_prompt=system, task=task, persistent=False)
        _replicas[_name] = r
    r.start()
    return r


def spawn_named(
    name: str,
    role: str = "custom",
    brief_path: Optional[str] = None,
    custom_prompt: Optional[str] = None,
    workspace: Optional[str] = None,
) -> Optional["Replica"]:
    """Spawn a named persistent replica (for client-facing meetings)."""
    with _lock:
        if name in _replicas:
            return _replicas[name]  # already running, return existing
        if not can_spawn():
            return None
        system = _build_system_prompt(role, brief_path, custom_prompt)
        r = Replica(
            name=name,
            role=role,
            system_prompt=system,
            persistent=True,
            brief_path=brief_path,
            workspace=workspace,
        )
        _replicas[name] = r
    r.start()
    return r


def get(name: str) -> Optional["Replica"]:
    return _replicas.get(name)


def stop(name: str) -> bool:
    r = _replicas.get(name)
    if r:
        r.stop()
        return True
    return False


def active() -> list["Replica"]:
    return list(_replicas.values())
