"""
replica.py — Spawn specialist sub-agents within VRAM limits.
Shared model weights. Isolated context per replica.
"""
import threading
from dataclasses import dataclass, field
from model import vram_free_mb, infer

CONTEXT_BUDGET_MB = 512  # VRAM per replica context
MAX_REPLICAS = 3          # hard cap

_replicas: list["Replica"] = []
_lock = threading.Lock()


ROLES = {
    "researcher": "You are a research specialist. Your job is to gather information, search for facts, and synthesise findings clearly.",
    "coder":      "You are a coding specialist. Your job is to write clean, working code based on the specification provided.",
    "reviewer":   "You are a code reviewer. Your job is to validate output for correctness, security, and quality.",
    "reporter":   "You are a reporting specialist. Your job is to summarise results concisely and deliver them clearly.",
}


@dataclass
class Replica:
    role: str
    task: str
    result: str = ""
    done: bool = False
    _thread: threading.Thread = field(default=None, repr=False)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        system = ROLES.get(self.role, "You are a helpful specialist.")
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": self.task},
        ]
        self.result = infer(messages, max_new_tokens=1024)
        self.done = True
        with _lock:
            _replicas.remove(self)


def can_spawn() -> bool:
    return len(_replicas) < MAX_REPLICAS and vram_free_mb() > CONTEXT_BUDGET_MB


def spawn(role: str, task: str) -> Replica | None:
    with _lock:
        if not can_spawn():
            return None
        r = Replica(role=role, task=task)
        _replicas.append(r)
    r.start()
    return r


def active() -> list[Replica]:
    return list(_replicas)
