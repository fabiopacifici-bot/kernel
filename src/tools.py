"""
tools.py — Kernel tool registry + executor.
Provides 4 tools for native function-calling: exec_shell, read_file, http_get, write_file.
"""
import subprocess
import json
import os
from pathlib import Path

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

KERNEL_WORKSPACE = str(Path.home() / ".kernel" / "workspace")
WORKSPACE = os.environ.get("KERNEL_WORKSPACE", KERNEL_WORKSPACE)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "exec_shell",
            "description": "Execute a shell command and return stdout/stderr. Use for running scripts, checking service health, git operations, file operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30)"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use for reading logs, configs, memory files, ROUTINE.md steps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or workspace-relative file path"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "Make an HTTP GET request and return the response body. Use for health checks, API calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 5)"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "Execute a named skill from the Kernel skill ecosystem. Use this when the user's request matches a skill's purpose (e.g. browser search, image generation, GitHub operations, security scan). Pass the user's original request as 'input'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "The skill name (e.g. 'browser-automation', 'github', 'security-scanner')"
                    },
                    "input": {
                        "type": "string",
                        "description": "The user's request or task to pass to the skill"
                    }
                },
                "required": ["skill_name", "input"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_routine",
            "description": "Execute a named routine from the Kernel routine library. Routines are multi-step procedures (e.g. morning-briefing, security-check, deploy, end-of-session).",
            "parameters": {
                "type": "object",
                "properties": {
                    "routine_name": {
                        "type": "string",
                        "description": "The routine name (e.g. 'morning-briefing', 'security-check', 'deploy', 'end-of-session')"
                    }
                },
                "required": ["routine_name"]
            }
        }
    }
]


def execute_tool(name: str, arguments: dict, workspace: str = WORKSPACE) -> str:
    """Execute a tool call and return the result as a string."""
    import os
    # Always expand ~ so subprocess.run cwd never gets a literal tilde
    workspace = os.path.expanduser(workspace)
    if name == "exec_shell":
        cmd = arguments.get("command")
        if not cmd:
            return "(error: exec_shell requires 'command' argument)"
        timeout = arguments.get("timeout", 30)
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=workspace
            )
            out = result.stdout.strip() or result.stderr.strip() or "(no output)"
            return out[:2000]
        except subprocess.TimeoutExpired:
            return f"(timeout after {timeout}s)"
        except Exception as e:
            return f"(error: {e})"

    elif name == "read_file":
        path = arguments.get("path")
        if not path:
            return "(error: read_file requires 'path' argument)"
        import os
        path = os.path.expanduser(path)
        if not path.startswith("/"):
            path = path.replace("~/", "~/"); path = f"{workspace}/{path}" if not path.startswith("/") else path
        try:
            with open(path) as f:
                return f.read()[:3000]
        except Exception as e:
            return f"(error reading {path}: {e})"

    elif name == "http_get":
        if not _HAS_REQUESTS:
            return "(error: requests library not available)"
        url = arguments.get("url")
        if not url:
            return "(error: http_get requires 'url' argument)"
        timeout = arguments.get("timeout", 5)
        try:
            r = _requests.get(url, timeout=timeout)
            return r.text[:1000]
        except Exception as e:
            return f"(error: {e})"

    elif name == "write_file":
        path = arguments.get("path")
        if not path:
            return "(error: write_file requires 'path' argument)"
        content = arguments.get("content")
        if content is None:
            return "(error: write_file requires 'content' argument)"
        if not path.startswith("/"):
            path = path.replace("~/", "~/"); path = f"{workspace}/{path}" if not path.startswith("/") else path
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Written to {path}"
        except Exception as e:
            return f"(error: {e})"

    elif name == "run_skill":
        skill_name = arguments.get("skill_name")
        input_text = arguments.get("input")
        if not skill_name:
            return "(error: run_skill requires 'skill_name' argument)"
        if not input_text:
            return "(error: run_skill requires 'input' argument)"
        try:
            import yaml as _yaml
            from pathlib import Path as _Path
            from skills import load_all as _load_skills, find as _find_skill, run as _run_skill
            from model import infer as _infer
            config_path = str(_Path(__file__).parent.parent / "config.yaml")
            cfg = _yaml.safe_load(open(config_path))
            skills_dir = cfg.get("skills_dir", "./skills")
            import os as _os
            skills_dir = _os.path.expanduser(skills_dir)
            all_skills = _load_skills(skills_dir)
            skill = _find_skill(skill_name, all_skills)
            if not skill:
                # Try partial match
                skill = next((s for s in all_skills if skill_name.lower() in s["name"].lower()), None)
            if not skill:
                available = [s["name"] for s in all_skills]
                return f"(error: skill '{skill_name}' not found. Available: {', '.join(available)})"
            return _run_skill(skill, input_text, _infer)
        except Exception as e:
            return f"(error running skill '{skill_name}': {e})"

    elif name == "run_routine":
        routine_name = arguments.get("routine_name")
        if not routine_name:
            return "(error: run_routine requires 'routine_name' argument)"
        try:
            import yaml as _yaml
            from pathlib import Path as _Path
            from routines import load_all as _load_routines, find as _find_routine, run as _run_routine
            from model import infer as _infer
            config_path = str(_Path(__file__).parent.parent / "config.yaml")
            cfg = _yaml.safe_load(open(config_path))
            routines_dir = cfg.get("routines_dir", "./routines")
            import os as _os
            routines_dir = _os.path.expanduser(routines_dir)
            all_routines = _load_routines(routines_dir)
            routine = _find_routine(routine_name, all_routines)
            if not routine:
                routine = next((r for r in all_routines if routine_name.lower() in r["name"].lower()), None)
            if not routine:
                available = [r["name"] for r in all_routines]
                return f"(error: routine '{routine_name}' not found. Available: {', '.join(available)})"
            return _run_routine(routine, _infer)
        except Exception as e:
            return f"(error running routine '{routine_name}': {e})"

    return f"Unknown tool: {name}"
