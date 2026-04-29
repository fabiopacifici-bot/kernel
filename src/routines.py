"""
routines.py — Load and execute ROUTINE.md files.
Reads steps, runs them in order via model.infer() or direct exec.
"""
import re
import os
import yaml
import subprocess
from pathlib import Path


def _parse_routine(path: Path) -> dict | None:
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except Exception:
        return None
    return {
        "name": fm.get("name", path.parent.name),
        "description": fm.get("description", ""),
        "trigger": fm.get("trigger", {}),
        "body": m.group(2).strip(),
        "path": str(path),
    }


def load_all(routines_dir="./routines") -> list[dict]:
    routines = []
    for r_md in Path(routines_dir).rglob("ROUTINE.md"):
        r = _parse_routine(r_md)
        if r:
            routines.append(r)
    return routines


def find(name: str, routines: list[dict]) -> dict | None:
    name = name.lower().strip()
    return next((r for r in routines if r["name"].lower() == name), None)


def _extract_shell_blocks(body: str) -> list[str]:
    """Extract bash/shell code blocks from routine body."""
    pattern = r'```(?:bash|shell)\n(.*?)```'
    return re.findall(pattern, body, re.DOTALL)


def _extract_context(body: str) -> str:
    """Extract non-code instruction text from routine body."""
    steps_match = re.search(r'## Steps(.*?)(?=\n## |\Z)', body, re.DOTALL)
    if not steps_match:
        return body
    steps = steps_match.group(1)
    clean = re.sub(r'```.*?```', '', steps, flags=re.DOTALL)
    clean = re.sub(r'#{1,4} ', '', clean)
    return clean.strip()


import os
from pathlib import Path
from tools import WORKSPACE as _DEFAULT_WORKSPACE

KERNEL_WORKSPACE = _DEFAULT_WORKSPACE


def run(routine: dict, infer_fn, workspace: str = None) -> str:
    if workspace is None:
        workspace = KERNEL_WORKSPACE
    """
    Execute a routine using native function-calling when the model is loaded,
    falling back to shell-block extraction + LLM summary otherwise.
    """
    from model import infer_with_tools, _model
    from tools import TOOLS

    body = routine.get("body", routine.get("instructions", ""))
    name = routine.get("name", "routine")

    # Prefer tool-calling mode when model is available
    if _model is not None:
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are Kernel, a local AI agent. Execute the '{name}' routine "
                    "step by step using the available tools. Complete all steps and provide "
                    "a concise summary at the end."
                ),
            },
            {
                "role": "user",
                "content": f"Run this routine:\n\n{body}",
            },
        ]
        return infer_with_tools(messages, TOOLS, workspace=workspace)

    # Fallback: extract shell blocks and summarise with plain infer
    shell_blocks = _extract_shell_blocks(body)
    context = _extract_context(body)

    if not shell_blocks:
        messages = [
            {"role": "system", "content": f"Execute this routine and report results:\n\n{body}"},
            {"role": "user", "content": f"Run the {name} routine."},
        ]
        return infer_fn(messages, max_new_tokens=1024)

    results = []
    for cmd in shell_blocks:
        cmd = cmd.strip()
        if not cmd:
            continue
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=30, cwd=workspace
            )
            output = result.stdout.strip() or result.stderr.strip() or "(no output)"
            label = f"$ {cmd[:60]}..." if len(cmd) > 60 else f"$ {cmd}"
            results.append(f"{label}\n{output[:500]}")
        except subprocess.TimeoutExpired:
            results.append(f"$ {cmd[:60]}...\n(timeout after 30s)")
        except Exception as e:
            results.append(f"$ {cmd[:60]}...\n(error: {e})")

    execution_log = "\n\n".join(results)
    messages = [
        {
            "role": "system",
            "content": (
                f"You are reporting the results of the '{name}' routine. "
                "Summarise the outputs below into a clear, structured report. "
                f"Be concise. Flag any issues or anomalies.\n\nContext:\n{context}"
            ),
        },
        {"role": "user", "content": f"Execution results:\n\n{execution_log}"},
    ]
    return infer_fn(messages, max_new_tokens=512)
