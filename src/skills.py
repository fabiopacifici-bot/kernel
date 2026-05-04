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
    from model import infer_with_tools, _model
    from tools import TOOLS

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
