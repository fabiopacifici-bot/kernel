"""
memory.py — Simple JSON-based session memory for Kernel.
Stores last N conversation turns to ~/.kernel_memory.json
"""
import json
from pathlib import Path

MEMORY_FILE = Path.home() / ".kernel_memory.json"
MAX_TURNS = 20  # each turn = user + assistant message pair


def load() -> list[dict]:
    """Return last MAX_TURNS message pairs (up to 10 messages)."""
    try:
        data = json.loads(MEMORY_FILE.read_text())
        messages = data.get("messages", [])
        # Keep last MAX_TURNS * 2 messages (user+assistant pairs)
        return messages[-(MAX_TURNS * 2):]
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []


def save(messages: list[dict]) -> None:
    """Persist messages to disk. Keeps last MAX_TURNS pairs."""
    trimmed = messages[-(MAX_TURNS * 2):]
    MEMORY_FILE.write_text(json.dumps({"messages": trimmed}, indent=2))


def clear() -> None:
    """Wipe memory file."""
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()


def show() -> str:
    """Return a human-readable summary of stored memory."""
    msgs = load()
    if not msgs:
        return "  No memory stored."
    lines = []
    for m in msgs:
        role = "You" if m["role"] == "user" else "Kernel"
        content = m["content"]
        if isinstance(content, list):
            # multimodal content
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        lines.append(f"  \033[92m{role}:\033[0m {str(content)[:120]}")
    return "\n".join(lines)
