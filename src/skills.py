"""
skills.py — Load and execute SKILL.md files.
Reads frontmatter, exposes skill list, runs via model.infer().
"""
import os
import re
import yaml
from pathlib import Path


def _parse_skill(path: Path) -> dict | None:
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
        "commands": fm.get("commands", fm.get("metadata", {}).get("commands", [])),
        "instructions": m.group(2).strip(),
        "path": str(path),
        "exec": fm.get("exec", None),  # direct script dispatch, bypasses LLM
    }


def _rglob_follow(base: Path, filename: str):
    """rglob that follows symlinks (Path.rglob doesn't in Python 3.12+)."""
    import os
    for root, dirs, files in os.walk(str(base), followlinks=True):
        if filename in files:
            yield Path(root) / filename


def load_all(skills_dir="./skills") -> list[dict]:
    skills = []
    for skill_md in _rglob_follow(Path(skills_dir), "SKILL.md"):
        s = _parse_skill(skill_md)
        if s:
            skills.append(s)
    return skills


def find(name: str, skills: list[dict]) -> dict | None:
    name = name.lower().strip()
    return next((s for s in skills if s["name"].lower() == name), None)


def run(skill: dict, user_input: str, infer_fn) -> str:
    """Execute a skill using native function-calling when model is loaded."""
    import subprocess
    from model import infer_with_tools, _model
    from tools import TOOLS

    # --- Direct exec dispatch (script-backed skills, no LLM) ---
    exec_template = skill.get("exec", None)
    if exec_template:
        # Substitute {args} with everything after the command trigger
        # e.g. "/markdown /path/to/file.pdf" → args = "/path/to/file.pdf"
        import shlex
        args = user_input.strip()
        # Strip leading slash-command word if present
        parts = args.split(None, 1)
        if len(parts) > 1 and parts[0].startswith("/"):
            args = parts[1]
        elif len(parts) == 1 and parts[0].startswith("/"):
            args = ""
        cmd = exec_template.replace("{args}", args).replace("{input}", user_input)
        skill_dir = str(Path(skill["path"]).parent)
        env = os.environ.copy()
        # Always inject KERNEL_SRC so scripts can find model_client
        env["KERNEL_SRC"] = str(Path(__file__).parent)
        # Load Kernel's own .env first (Telegram creds etc.)
        kernel_env = Path(skill["path"]).parent
        for _ in range(5):  # walk up max 5 levels looking for kernel .env
            kernel_env = kernel_env.parent
            candidate = kernel_env / ".env"
            if candidate.exists() and "MICROCLAW_TELEGRAM" in candidate.read_text():
                for line in candidate.read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        env.setdefault(k.strip(), v.strip())  # don't override existing
                break
        # Also load well-known Kernel .env path directly
        kernel_default_env = Path("/home/pacificDev/.openclaw/workspace/repositories/kernel/.env")
        if kernel_default_env.exists():
            for line in kernel_default_env.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip())
        # Load skill .env if present (can override kernel env)
        env_path = Path(skill_dir) / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        print(f"[skills] Direct exec: {cmd}")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=300, env=env, cwd=skill_dir
            )
            output = (result.stdout + result.stderr).strip()
            return output if output else "✅ Done."
        except subprocess.TimeoutExpired:
            return "❌ Script timed out after 300s."
        except Exception as e:
            return f"❌ Exec error: {e}"

    instructions = skill.get("instructions", "")
    name = skill.get("name", "skill")

    if _model is not None:
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are Kernel. Execute the '{name}' skill using the available tools.\n\n"
                    f"Skill instructions:\n{instructions}"
                ),
            },
            {"role": "user", "content": user_input},
        ]
        return infer_with_tools(messages, TOOLS)

    # Fallback to plain infer
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": user_input},
    ]
    return infer_fn(messages)
