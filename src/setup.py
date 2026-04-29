"""
setup.py — First-boot workspace setup for Kernel.
Run once when ~/.kernel/workspace doesn't exist.
"""
from pathlib import Path
import os

KERNEL_HOME = Path.home() / ".kernel"
WORKSPACE   = KERNEL_HOME / "workspace"
MEMORY_FILE = KERNEL_HOME / "memory.json"
CONFIG_DIRS = ["memory", "tmp", "notes", "scripts"]


def setup_workspace():
    if WORKSPACE.exists():
        return False  # already set up

    print("[setup] First boot — creating Kernel workspace...")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    for d in CONFIG_DIRS:
        (WORKSPACE / d).mkdir(exist_ok=True)

    # Write IDENTITY.md
    (WORKSPACE / "IDENTITY.md").write_text("""# Kernel Identity

Name: Kernel
Previous name: MicroClaw (renamed April 26, 2026)
Role: Local-first AI agent — system tier under Olly (OpenClaw)
Author: configured via kernel init
Stack: Gemma 4 E2B-it, FastAPI, Python
Home: localhost:8769

## Workspace access
- Own workspace: ~/.kernel/workspace (read/write freely)
- Olly workspace: ~/.openclaw/workspace (READ ONLY unless Olly offline + user approved)
- Other agent workspaces: READ ONLY unless explicitly authorized

## Never do without explicit authorization
- Write to memory/ or MEMORY.md in any agent workspace
- Commit or push from any agent workspace
- Modify .openclaw/ config
- Delete files in any workspace
""")

    # Write README
    (WORKSPACE / "README.md").write_text("# Kernel Workspace\nKernel's private working directory.\n")

    print(f"[setup] Workspace created at {WORKSPACE}")
    return True


if __name__ == "__main__":
    setup_workspace()
