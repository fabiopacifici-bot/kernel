"""
memory.py — Two-tier chat persistence for Kernel.

Tier 1 (hot):  ~/.kernel_memory.json        — sliding context window (last N turns, fast load)
Tier 2 (cold): ~/.kernel/workspace/chat_history.db — SQLite, every turn, permanent

On load()  : return JSON window; if empty, seed from SQLite.
On save()  : append new messages to SQLite, refresh JSON window.
On clear() : wipe JSON window only (SQLite history is permanent).
"""
import json
import sqlite3
import uuid
import os
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
MEMORY_FILE = Path.home() / ".kernel_memory.json"
DB_DIR      = Path.home() / ".kernel" / "workspace"
DB_FILE     = DB_DIR / "chat_history.db"
DB_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────────────
MAX_TURNS   = 20   # pairs kept in JSON window
BOT_NAME    = "kernel"

# ── Session ID ────────────────────────────────────────────────────────────────
# One session per process run — stable across the life of the bot instance.
_SESSION_ID: str | None = None

def _session_id() -> str:
    global _SESSION_ID
    if _SESSION_ID is None:
        _SESSION_ID = str(uuid.uuid4())
    return _SESSION_ID


# ── SQLite helpers ────────────────────────────────────────────────────────────
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            bot        TEXT    NOT NULL DEFAULT 'kernel',
            session_id TEXT    NOT NULL,
            role       TEXT    NOT NULL CHECK(role IN ('user','assistant','system')),
            content    TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bot_session ON messages(bot, session_id, id);
        CREATE INDEX IF NOT EXISTS idx_bot_created ON messages(bot, created_at);
    """)
    conn.commit()


def _append_to_db(conn: sqlite3.Connection, messages: list[dict], session_id: str) -> None:
    """Insert only messages that aren't already stored (idempotent)."""
    # Count existing rows for this session to detect new ones
    existing = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE bot=? AND session_id=?",
        (BOT_NAME, session_id)
    ).fetchone()[0]
    new_msgs = messages[existing:]  # only truly new ones
    ts = datetime.now(timezone.utc).isoformat()
    for m in new_msgs:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        conn.execute(
            "INSERT INTO messages (bot, session_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (BOT_NAME, session_id, m["role"], content, ts)
        )
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def load() -> list[dict]:
    """Return last MAX_TURNS message pairs for the context window.

    Order of precedence:
    1. JSON window (fast path — same session continuing)
    2. SQLite last N turns (cold start / new process)
    """
    # Fast path: JSON window exists and has content
    try:
        data = json.loads(MEMORY_FILE.read_text())
        msgs = data.get("messages", [])
        if msgs:
            return msgs[-(MAX_TURNS * 2):]
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Cold start: seed from SQLite
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT role, content FROM messages
               WHERE bot=?
               ORDER BY id DESC LIMIT ?""",
            (BOT_NAME, MAX_TURNS * 2)
        ).fetchall()
        conn.close()
        msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        if msgs:
            # Warm up the JSON window
            MEMORY_FILE.write_text(json.dumps({"messages": msgs}, indent=2))
        return msgs
    except Exception:
        return []


def save(messages: list[dict]) -> None:
    """Persist messages. Appends new turns to SQLite; refreshes JSON window."""
    trimmed = messages[-(MAX_TURNS * 2):]

    # JSON window (hot cache)
    MEMORY_FILE.write_text(json.dumps({"messages": trimmed}, indent=2))

    # SQLite (permanent record)
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        _append_to_db(conn, messages, _session_id())
        conn.close()
    except Exception as e:
        print(f"[memory] SQLite write failed: {e}")


def clear() -> None:
    """Clear the JSON window. SQLite history is preserved."""
    if MEMORY_FILE.exists():
        MEMORY_FILE.unlink()


def clear_all() -> None:
    """Wipe everything — JSON window AND SQLite history for this bot."""
    clear()
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        conn.execute("DELETE FROM messages WHERE bot=?", (BOT_NAME,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[memory] SQLite clear failed: {e}")


def history(limit: int = 50) -> list[dict]:
    """Return up to `limit` most recent turns from SQLite (across all sessions)."""
    try:
        conn = _get_conn()
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT role, content, session_id, created_at
               FROM messages WHERE bot=?
               ORDER BY id DESC LIMIT ?""",
            (BOT_NAME, limit * 2)
        ).fetchall()
        conn.close()
        return [
            {"role": r["role"], "content": r["content"],
             "session_id": r["session_id"], "created_at": r["created_at"]}
            for r in reversed(rows)
        ]
    except Exception:
        return []


def show() -> str:
    """Human-readable summary of the current context window."""
    msgs = load()
    if not msgs:
        return "  No memory stored."
    lines = []
    for m in msgs:
        role = "You" if m["role"] == "user" else "Kernel"
        content = m["content"]
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        lines.append(f"  \033[92m{role}:\033[0m {str(content)[:120]}")
    return "\n".join(lines)
