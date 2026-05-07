"""
model_server.py — Long-lived model server process.

Loads Gemma 4 once, stays alive, owns GPU VRAM.
Exposes JSON-RPC over Unix socket /tmp/kernel_model.sock.
API and Telegram bot connect to it and restart freely.

Usage:
    python3 src/model_server.py [--config config.yaml]
"""
import json
import os
import signal
import socket
import socketserver
import sys
import threading
import traceback
from pathlib import Path

# Allow running from src/ or repo root
_src = os.path.dirname(os.path.abspath(__file__))
if _src not in sys.path:
    sys.path.insert(0, _src)

import yaml

SOCKET_PATH = "/tmp/kernel_model.sock"

_model = None
_processor = None
_config = None
_drafter = None  # MTP drafter for speculative decoding
_lazy_config_path = "config.yaml"  # set at startup, used by lazy load in handlers


def _load_config(path="config.yaml"):
    global _config
    with open(path) as f:
        _config = yaml.safe_load(f)
    return _config


def _ensure_model():
    """Called by inference handlers — loads model lazily if not yet loaded."""
    if _model is None:
        _load_model(_lazy_config_path)


def _load_model(config_path="config.yaml"):
    """Load model (and optional drafter) once and store globally."""
    global _model, _processor, _config, _drafter
    if _model is not None:
        return

    import torch
    from transformers import AutoProcessor, AutoModelForImageTextToText, AutoModelForCausalLM

    cfg = _load_config(config_path)
    model_source = os.environ.get("MODEL_SOURCE", "local")

    if model_source == "docker-hub":
        model_path = os.environ.get("MODEL_ID", cfg["model"]["name"])
        print(f"[model_server] Docker Hub mode — {model_path}", flush=True)
    else:
        model_path = cfg["model"]["path"]

    device = cfg["model"].get("device", "cuda")
    dtype = getattr(torch, cfg["model"].get("dtype", "bfloat16"))

    # VRAM guard: ensure enough free VRAM before loading
    if torch.cuda.is_available():
        free_bytes, _ = torch.cuda.mem_get_info()
        free_mb = free_bytes // (1024 * 1024)
        min_required_mb = 6000  # Gemma 4 E2B needs ~5.5GB
        if free_mb < min_required_mb:
            msg = (f"[model_server] VRAM guard: only {free_mb}MB free, "
                   f"need {min_required_mb}MB. Refusing to load to prevent OOM.")
            print(msg, flush=True)
            raise RuntimeError(msg)
        print(f"[model_server] VRAM check passed: {free_mb}MB free", flush=True)

    print(f"[model_server] Loading {model_path} on {device} ({dtype}) ...", flush=True)
    _processor = AutoProcessor.from_pretrained(model_path)
    _model = AutoModelForImageTextToText.from_pretrained(
        model_path, dtype=dtype, device_map="auto"
    )
    print("[model_server] Model loaded.", flush=True)

    # Load MTP drafter for speculative decoding if configured
    drafter_path = cfg["model"].get("drafter_path")
    use_speculative = cfg["model"].get("speculative_decoding", False)
    if drafter_path and use_speculative:
        try:
            print(f"[model_server] Loading drafter for speculative decoding: {drafter_path}", flush=True)
            _drafter = AutoModelForCausalLM.from_pretrained(
                drafter_path, dtype=dtype, device_map="auto"
            )
            print("[model_server] Drafter loaded — speculative decoding enabled (~2x speedup).", flush=True)
        except Exception as e:
            print(f"[model_server] WARNING: drafter load failed ({e}) — falling back to standard decoding.", flush=True)
            _drafter = None
    else:
        _drafter = None


# ---------------------------------------------------------------------------
# RPC handler functions
# ---------------------------------------------------------------------------

def _handle_infer(params: dict) -> dict:
    """Standard chat inference."""
    _ensure_model()
    import torch

    messages = params.get("messages", [])
    max_new_tokens = params.get("max_new_tokens", 512)

    # Guard: if messages is a plain string, wrap it as a user message
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    # Validate messages is a list of dicts
    if not isinstance(messages, list):
        return {"error": f"'messages' must be a list, got {type(messages).__name__}"}

    formatted = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else m["role"]
        content = m["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        formatted.append({"role": role, "content": content})

    text = _processor.apply_chat_template(
        formatted,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = _processor(text=text, return_tensors="pt").to(_model.device)
    input_len = inputs["input_ids"].shape[-1]

    with torch.no_grad():
        _gen_kwargs = dict(max_new_tokens=max_new_tokens, do_sample=False)
        if _drafter is not None:
            _gen_kwargs["assistant_model"] = _drafter
        out = _model.generate(inputs["input_ids"], **_gen_kwargs)
    result = _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()
    return {"result": result}


def _handle_infer_with_tools(params: dict, send_line) -> dict:
    """
    Agentic tool-calling loop.
    Streams intermediate steps back as {"type": "step", ...} lines,
    then returns final {"type": "result", "result": "..."}.
    """
    _ensure_model()
    import json
    import torch
    from tools import execute_tool

    messages = params["messages"]
    tools = params.get("tools", [])
    workspace = os.path.expanduser(params.get("workspace", "~/.openclaw/workspace"))
    max_steps = params.get("max_steps", 15)

    current_messages = []
    for m in messages:
        role = "model" if m["role"] == "assistant" else m["role"]
        content = m["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        current_messages.append({"role": role, "content": content})

    for step in range(max_steps):
        text = _processor.apply_chat_template(
            current_messages,
            tools=tools,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = _processor(text=text, return_tensors="pt").to(_model.device)
        input_len = inputs["input_ids"].shape[-1]

        with torch.no_grad():
            _gen_kwargs = dict(max_new_tokens=512, do_sample=False)
            if _drafter is not None:
                _gen_kwargs["assistant_model"] = _drafter
            out = _model.generate(**inputs, **_gen_kwargs)

        response_raw = _processor.decode(out[0][input_len:], skip_special_tokens=False)
        response_clean = _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()

        # Import parse helper from model.py
        from model import _parse_tool_call
        tool_call = _parse_tool_call(response_raw)
        if tool_call is None:
            return {"type": "result", "result": response_clean}

        tool_name = tool_call.get("name") or tool_call.get("function", {}).get("name", "")
        tool_args = tool_call.get("arguments") or tool_call.get("function", {}).get("arguments", {})
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {}

        result_str = execute_tool(tool_name, tool_args, workspace=workspace)

        # Stream step back to client
        step_line = json.dumps({
            "type": "step",
            "step": step + 1,
            "tool": tool_name,
            "args": tool_args,
            "result": result_str,
        })
        send_line(step_line)

        current_messages.append({
            "role": "model",
            "content": [{"type": "text", "text": response_clean or f"[called {tool_name}]"}]
        })
        current_messages.append({
            "role": "user",
            "content": [{"type": "text", "text": f"Tool `{tool_name}` result:\n{result_str}\n\nPlease provide your final answer based on the tool result above."}]
        })

    return {"type": "result", "result": "(max steps reached)"}


def _handle_infer_with_image(params: dict) -> dict:
    """Multimodal image inference."""
    _ensure_model()
    import torch
    from PIL import Image

    image_path = params["image_path"]
    prompt = params["prompt"]
    max_new_tokens = params.get("max_new_tokens", 512)

    img = Image.open(image_path).convert("RGB")
    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": img},
            {"type": "text", "text": prompt},
        ]}
    ]
    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = _processor(text=text, images=[img], return_tensors="pt").to(_model.device)
    input_len = inputs["input_ids"].shape[-1]
    with torch.no_grad():
        _gen_kwargs = dict(max_new_tokens=max_new_tokens, do_sample=False)
        if _drafter is not None:
            _gen_kwargs["assistant_model"] = _drafter
        out = _model.generate(**inputs, **_gen_kwargs)
    result = _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()
    return {"result": result}
def _handle_infer_with_audio(params: dict) -> dict:
    """Multimodal audio inference."""
    _ensure_model()
    import torch
    import soundfile as sf
    import numpy as np

    audio_path = params["audio_path"]
    prompt = params.get("prompt", "Transcribe this audio.")
    max_new_tokens = params.get("max_new_tokens", 512)

    try:
        audio_array, sample_rate = sf.read(audio_path, dtype="float32")
    except Exception:
        import subprocess
        import tempfile
        tmp = tempfile.mktemp(suffix=".wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", tmp],
            capture_output=True, timeout=30
        )
        audio_array, sample_rate = sf.read(tmp, dtype="float32")
        os.unlink(tmp)

    if audio_array.ndim > 1:
        audio_array = audio_array.mean(axis=1)

    messages = [
        {"role": "user", "content": [
            {"type": "audio", "audio": {"array": audio_array, "sampling_rate": sample_rate}},
            {"type": "text", "text": prompt},
        ]}
    ]
    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = _processor(
        text=text,
        audio=[{"array": audio_array, "sampling_rate": sample_rate}],
        return_tensors="pt"
    ).to(_model.device)
    input_len = inputs["input_ids"].shape[-1]
    with torch.no_grad():
        _gen_kwargs = dict(max_new_tokens=max_new_tokens, do_sample=False)
        if _drafter is not None:
            _gen_kwargs["assistant_model"] = _drafter
        out = _model.generate(**inputs, **_gen_kwargs)
    result = _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()
    return {"result": result}


def _handle_vram_free_mb(params: dict) -> dict:
    import torch
    if torch.cuda.is_available():
        free, _ = torch.cuda.mem_get_info()
        return {"vram_free_mb": free // (1024 * 1024)}
    import psutil
    return {"vram_free_mb": psutil.virtual_memory().available // (1024 * 1024)}


def _handle_health(params: dict) -> dict:
    import torch
    vram_free = _handle_vram_free_mb({}).get("vram_free_mb", 0)
    model_name = _config["model"].get("name", "unknown") if _config else "unknown"
    vram_warning = vram_free < 2000
    return {"status": "ready", "model": model_name, "vram_free_mb": vram_free, "vram_warning": vram_warning}


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

class _RequestHandler(socketserver.StreamRequestHandler):
    """
    Handle a single client connection.
    Reads one newline-delimited JSON request, processes it, writes response(s).
    For infer_with_tools, multiple lines may be written (steps + final result).
    """

    def handle(self):
        try:
            raw = self.rfile.readline()
            if not raw:
                return
            request = json.loads(raw.decode("utf-8").strip())
            method = request.get("method", "")
            params = request.get("params", {})

            def send_line(data: str):
                self.wfile.write((data + "\n").encode("utf-8"))
                self.wfile.flush()

            if method == "infer":
                resp = _handle_infer(params)
                send_line(json.dumps(resp))

            elif method == "infer_with_tools":
                # Streams step lines then final result
                resp = _handle_infer_with_tools(params, send_line)
                send_line(json.dumps(resp))

            elif method == "infer_with_image":
                resp = _handle_infer_with_image(params)
                send_line(json.dumps(resp))

            elif method == "infer_with_audio":
                resp = _handle_infer_with_audio(params)
                send_line(json.dumps(resp))

            elif method == "vram_free_mb":
                resp = _handle_vram_free_mb(params)
                send_line(json.dumps(resp))

            elif method == "health":
                resp = _handle_health(params)
                send_line(json.dumps(resp))

            else:
                send_line(json.dumps({"error": f"unknown method: {method}"}))

        except Exception as exc:
            tb = traceback.format_exc()
            print(f"[model_server] handler error: {exc}\n{tb}", flush=True)
            try:
                self.wfile.write((json.dumps({"error": str(exc)}) + "\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass


class _ThreadingUnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """Threaded Unix socket server — handles concurrent requests."""
    daemon_threads = True
    allow_reuse_address = True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Kernel model server")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--socket", default=None, help="Override socket path")
    parser.add_argument("--lazy", action="store_true", help="Defer model load to first inference request (reduces boot RAM)")
    args = parser.parse_args()

    socket_path = args.socket or SOCKET_PATH

    # Read socket path from config if present
    cfg = _load_config(args.config)
    if cfg.get("model_server", {}).get("socket"):
        socket_path = cfg["model_server"]["socket"]
    if args.socket:
        socket_path = args.socket  # CLI flag wins

    # Handle existing socket: if live, exit cleanly; if stale, remove it
    if os.path.exists(socket_path):
        # Try connecting to see if it's alive
        try:
            test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            test_sock.settimeout(2)
            test_sock.connect(socket_path)
            test_sock.close()
            print(f"[model_server] Socket {socket_path} is already live — another instance is running. Exiting.", flush=True)
            sys.exit(0)
        except (ConnectionRefusedError, OSError):
            # Stale socket — remove it and proceed
            print(f"[model_server] Removing stale socket {socket_path}", flush=True)
            os.unlink(socket_path)

    # Load model — eager by default, deferred if --lazy
    global _lazy_config_path
    _lazy_config_path = os.path.abspath(args.config)  # absolute path — safe regardless of cwd
    if args.lazy:
        print(f"[model_server] Lazy mode: model will load on first inference request.", flush=True)
    else:
        _load_model(args.config)

    # Signal handlers for clean shutdown
    server = None

    def _shutdown(signum, frame):
        print(f"\n[model_server] Caught signal {signum}, shutting down...", flush=True)
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server = _ThreadingUnixServer(socket_path, _RequestHandler)
    os.chmod(socket_path, 0o600)

    print(f"[model_server] Ready on {socket_path}", flush=True)
    sys.stdout.flush()

    server.serve_forever()


if __name__ == "__main__":
    main()
