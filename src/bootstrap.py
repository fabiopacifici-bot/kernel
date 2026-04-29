"""
bootstrap.py — Pull skills and routines from ecosystem repos on startup.

Ecosystem structure:
  ~/.kernel/ecosystem/
    community/     ← fabiopacifici-bot/microclaw_community_ecosystem
    private/       ← fabiopacifici-bot/microclaw_private_ecosystem
    third-party/   ← skills cloned from other agents (e.g. Olly's workspace)

Skills/routines are discovered by scanning all subdirs of ~/.kernel/ecosystem/
"""
import os
import subprocess
import yaml
from pathlib import Path


ECOSYSTEM_ROOT = Path.home() / ".kernel" / "ecosystem"


def _pull_or_clone(repo: str, branch: str, local_path: Path) -> bool:
    """Clone or pull a GitHub repo. Returns True on success."""
    try:
        if local_path.exists():
            result = subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=local_path, capture_output=True, text=True, timeout=30
            )
            msg = result.stdout.strip() or result.stderr.strip() or "up to date"
            print(f"[bootstrap] {local_path.name}: {msg}")
        else:
            repo_url = f"https://github.com/{repo}.git"
            result = subprocess.run(
                ["git", "clone", "--depth=1", "-b", branch, repo_url, str(local_path)],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                print(f"[bootstrap] {local_path.name}: clone failed — {result.stderr.strip()}")
                return False
            print(f"[bootstrap] {local_path.name}: cloned from {repo_url}")
        return True
    except Exception as e:
        print(f"[bootstrap] {local_path.name}: error — {e}")
        return False


def bootstrap(config: dict) -> dict:
    """
    Pull community + private ecosystem repos.
    Returns dict with counts: {skills: N, routines: N}
    """
    eco = config.get("ecosystem", {})
    if not eco.get("bootstrap_on_startup", False):
        return {"skills": 0, "routines": 0}

    ECOSYSTEM_ROOT.mkdir(parents=True, exist_ok=True)
    (ECOSYSTEM_ROOT / "third-party").mkdir(exist_ok=True)

    branch = eco.get("branch", "main")
    counts = {"skills": 0, "routines": 0}

    for repo_key in ["community", "private"]:
        repo = eco.get(repo_key)
        if not repo:
            continue
        local_path = ECOSYSTEM_ROOT / repo_key
        _pull_or_clone(repo, branch, local_path)

    return counts


def search(query: str) -> list[dict]:
    """
    Search all ecosystem tiers for skills/routines matching query.
    Returns list of {name, description, type, source, path}
    """
    results = []
    query_lower = query.lower()

    for tier in ECOSYSTEM_ROOT.iterdir():
        if not tier.is_dir():
            continue
        source = tier.name  # community, private, third-party

        # Skills
        skills_dir = tier / "skills"
        if skills_dir.exists():
            for skill_dir in skills_dir.iterdir():
                if (skill_dir / "SKILL.md").exists():
                    skill_md = (skill_dir / "SKILL.md").read_text()
                    name = skill_dir.name
                    # Extract description from frontmatter
                    desc = ""
                    for line in skill_md.splitlines():
                        if line.startswith("description:"):
                            desc = line.replace("description:", "").strip()
                            break
                    if query_lower in name.lower() or query_lower in desc.lower() or query_lower in skill_md.lower()[:500]:
                        results.append({
                            "name": name,
                            "description": desc,
                            "type": "skill",
                            "source": source,
                            "path": str(skill_dir),
                        })

        # Routines
        routines_dir = tier / "routines"
        if routines_dir.exists():
            for routine_dir in routines_dir.iterdir():
                if (routine_dir / "ROUTINE.md").exists():
                    routine_md = (routine_dir / "ROUTINE.md").read_text()
                    name = routine_dir.name
                    desc = ""
                    for line in routine_md.splitlines():
                        if line.startswith("description:"):
                            desc = line.replace("description:", "").strip()
                            break
                    if query_lower in name.lower() or query_lower in desc.lower():
                        results.append({
                            "name": name,
                            "description": desc,
                            "type": "routine",
                            "source": source,
                            "path": str(routine_dir),
                        })

    return results


def install(name: str, item_type: str = None) -> dict:
    """
    Install a skill or routine from ecosystem into active dirs.
    Returns {ok: bool, message: str}
    """
    # Find it first
    results = search(name)
    matches = [r for r in results if r["name"].lower() == name.lower()]
    if item_type:
        matches = [r for r in matches if r["type"] == item_type]

    if not matches:
        return {"ok": False, "message": f"No skill or routine named '{name}' found in ecosystem. Try /search {name}"}
    if len(matches) > 1:
        # Prefer community over third-party, private over community
        order = {"private": 0, "community": 1, "third-party": 2}
        matches.sort(key=lambda x: order.get(x["source"], 99))

    item = matches[0]
    src_path = Path(item["path"])

    # Determine destination
    cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}

    if item["type"] == "skill":
        dst_dir = Path(os.path.expanduser(cfg.get("skills_dir", str(ECOSYSTEM_ROOT))))
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / item["name"]
    else:
        dst_dir = Path(os.path.expanduser(cfg.get("routines_dir", str(ECOSYSTEM_ROOT))))
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / item["name"]

    if dst.exists():
        return {"ok": True, "message": f"'{name}' is already installed (source: {item['source']})"}

    # Symlink (ecosystem items) or note already in place
    try:
        dst.symlink_to(src_path.resolve())
        return {"ok": True, "message": f"✅ Installed {item['type']} '{name}' from {item['source']}"}
    except Exception as e:
        return {"ok": False, "message": f"Install failed: {e}"}


def clone_from_agent(src_path: str, name: str = None) -> dict:
    """
    Copy a skill/routine from another agent's workspace into third-party tier.
    src_path: path to the skill/routine directory on this machine
    """
    import shutil
    src = Path(src_path)
    if not src.exists():
        return {"ok": False, "message": f"Source path not found: {src_path}"}

    skill_name = name or src.name
    dst = ECOSYSTEM_ROOT / "third-party" / "skills" / skill_name

    if dst.exists():
        return {"ok": True, "message": f"'{skill_name}' already in third-party ecosystem"}

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dst)
        return {"ok": True, "message": f"✅ Cloned '{skill_name}' into third-party ecosystem. Run /install {skill_name} to activate."}
    except Exception as e:
        return {"ok": False, "message": f"Clone failed: {e}"}
