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
    }
]


def execute_tool(name: str, arguments: dict, workspace: str = WORKSPACE) -> str:
    """Execute a tool call and return the result as a string."""
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

    return f"Unknown tool: {name}"
