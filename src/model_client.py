"""
model_client.py — Drop-in client for model_server.py.

Exposes the same public API as model.py but routes calls through the
persistent Unix socket server instead of loading the model in-process.

Falls back transparently when the server is not running
(caller should check is_server_running() first).
"""
import json
import os
import socket
import time
from typing import Callable, List, Optional

SOCKET_PATH = "/tmp/kernel_model.sock"
REQUEST_TIMEOUT = 300  # seconds — model inference can be slow


def _get_socket_path() -> str:
    """Return socket path, preferring config override if available."""
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        return cfg.get("model_server", {}).get("socket", SOCKET_PATH)
    except Exception:
        return SOCKET_PATH


def is_server_running() -> bool:
    """Return True if the model server socket exists and accepts connections."""
    sock_path = _get_socket_path()
    if not os.path.exists(sock_path):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(sock_path)
        s.close()
        return True
    except Exception:
        return False


def _call(request: dict, timeout: float = REQUEST_TIMEOUT) -> List[dict]:
    """
    Send a JSON-RPC request to the model server.
    Returns a list of parsed response lines (for streaming methods like infer_with_tools).
    Raises on connection failure or timeout.
    """
    sock_path = _get_socket_path()
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)

    payload = (json.dumps(request) + "\n").encode("utf-8")
    s.sendall(payload)

    # Read response lines until connection closes
    buf = b""
    lines = []
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line:
                    lines.append(json.loads(line.decode("utf-8")))
    except socket.timeout:
        raise TimeoutError(f"model_server request timed out after {timeout}s")
    finally:
        s.close()

    return lines


# ---------------------------------------------------------------------------
# Public API — mirrors model.py
# ---------------------------------------------------------------------------

def infer(messages: list, max_new_tokens: int = 512) -> str:
    """Run chat inference via model server. Returns response string."""
    try:
        resp_lines = _call({
            "method": "infer",
            "params": {"messages": messages, "max_new_tokens": max_new_tokens},
        })
        if resp_lines:
            resp = resp_lines[0]
            if "error" in resp:
                return f"[model_server error] {resp['error']}"
            return resp.get("result", "")
        return ""
    except TimeoutError as e:
        return f"[model_server timeout] {e}"
    except Exception as e:
        return f"[model_client error] {e}"


def infer_with_tools(
    messages: list,
    tools: list,
    workspace: str = "~/.openclaw/workspace",
    max_steps: int = 15,
    step_callback: Optional[Callable] = None,
) -> str:
    """
    Agentic tool-calling loop via model server.
    Server streams step lines then final result.
    step_callback(step_num, tool_name, tool_args, result) called per step.
    """
    try:
        resp_lines = _call({
            "method": "infer_with_tools",
            "params": {
                "messages": messages,
                "tools": tools,
                "workspace": workspace,
                "max_steps": max_steps,
            },
        })
        final_result = ""
        for line in resp_lines:
            if line.get("type") == "step":
                if step_callback:
                    step_callback(
                        line.get("step", 0),
                        line.get("tool", ""),
                        line.get("args", {}),
                        line.get("result", ""),
                    )
            elif line.get("type") == "result":
                final_result = line.get("result", "")
            elif "error" in line:
                return f"[model_server error] {line['error']}"
            else:
                # Fallback: plain result dict
                final_result = line.get("result", final_result)
        return final_result
    except TimeoutError as e:
        return f"[model_server timeout] {e}"
    except Exception as e:
        return f"[model_client error] {e}"


def infer_with_image(image_path: str, prompt: str, max_new_tokens: int = 512) -> str:
    """Multimodal image inference via model server."""
    try:
        resp_lines = _call({
            "method": "infer_with_image",
            "params": {
                "image_path": image_path,
                "prompt": prompt,
                "max_new_tokens": max_new_tokens,
            },
        })
        if resp_lines:
            resp = resp_lines[0]
            if "error" in resp:
                return f"[model_server error] {resp['error']}"
            return resp.get("result", "")
        return ""
    except TimeoutError as e:
        return f"[model_server timeout] {e}"
    except Exception as e:
        return f"[model_client error] {e}"


def infer_with_audio(
    audio_path: str,
    prompt: str = "Transcribe this audio.",
    max_new_tokens: int = 512,
) -> str:
    """Multimodal audio inference via model server."""
    try:
        resp_lines = _call({
            "method": "infer_with_audio",
            "params": {
                "audio_path": audio_path,
                "prompt": prompt,
                "max_new_tokens": max_new_tokens,
            },
        })
        if resp_lines:
            resp = resp_lines[0]
            if "error" in resp:
                return f"[model_server error] {resp['error']}"
            return resp.get("result", "")
        return ""
    except TimeoutError as e:
        return f"[model_server timeout] {e}"
    except Exception as e:
        return f"[model_client error] {e}"


def vram_free_mb() -> int:
    """Return free VRAM in MB via model server."""
    try:
        resp_lines = _call({
            "method": "vram_free_mb",
            "params": {},
        })
        if resp_lines:
            resp = resp_lines[0]
            if "error" in resp:
                return 0
            return int(resp.get("vram_free_mb", 0))
        return 0
    except Exception:
        return 0


def health() -> dict:
    """Return health dict from model server."""
    try:
        resp_lines = _call({
            "method": "health",
            "params": {},
        })
        return resp_lines[0] if resp_lines else {}
    except Exception as e:
        return {"error": str(e)}


def swap_model(model_path: str, drafter_path: str = None, dtype: str = "bfloat16") -> dict:
    """Hot-swap the loaded model. drafter_path=None keeps config default, '' disables."""
    try:
        params = {"model_path": model_path, "dtype": dtype}
        if drafter_path is not None:
            params["drafter_path"] = drafter_path
        resp_lines = _call({"method": "swap_model", "params": params}, timeout=300)
        return resp_lines[0] if resp_lines else {"error": "no response"}
    except Exception as e:
        return {"error": str(e)}
