"""
model.py — Load Gemma 4 E2B-it and expose inference + audio.
One model loaded once. All agents share it.
"""
import os
import torch
import yaml
from pathlib import Path
from transformers import AutoProcessor, AutoModelForImageTextToText

_model = None
_processor = None
_config = None


def load_config(path="config.yaml"):
    global _config
    with open(path) as f:
        _config = yaml.safe_load(f)
    return _config


def load(config_path="config.yaml"):
    global _model, _processor, _config
    if _model:
        return _model, _processor

    cfg = load_config(config_path)
    model_source = os.environ.get("MODEL_SOURCE", "local")

    if model_source == "docker-hub":
        model_path = os.environ.get("MODEL_ID", cfg["model"]["name"])
        print(f"[model] Docker Hub mode — using {model_path}")
    else:
        model_path = cfg["model"]["path"]

    device = cfg["model"].get("device", "cuda" if torch.cuda.is_available() else "cpu")
    dtype = getattr(torch, cfg["model"].get("dtype", "bfloat16"))

    print(f"[model] Loading {model_path} on {device} ({dtype})")
    _processor = AutoProcessor.from_pretrained(model_path)
    _model = AutoModelForImageTextToText.from_pretrained(
        model_path, dtype=dtype, device_map="auto"
    )
    print(f"[model] Ready")
    return _model, _processor


def infer(messages: list, max_new_tokens=512) -> str:
    """Run a chat completion. messages = OpenAI-style list with string content."""
    # Gemma 4 uses 'model' not 'assistant' for assistant role
    formatted = []
    for m in messages:
        role = m["role"]
        if role == "assistant":
            role = "model"
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
        out = _model.generate(inputs["input_ids"], max_new_tokens=max_new_tokens, do_sample=False)
    return _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()


def infer_with_tools(
    messages: list,
    tools: list,
    workspace: str = "~/.openclaw/workspace",
    max_steps: int = 15,
    step_callback=None,
) -> str:
    """
    Agentic inference loop with native function calling.
    Gemma emits tool calls → we execute them → feed results back → repeat until final answer.
    Returns the final text response.
    """
    from tools import execute_tool
    import json

    current_messages = []
    for m in messages:
        role = m["role"]
        if role == "assistant":
            role = "model"
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
            out = _model.generate(
                **inputs,
                max_new_tokens=512,
                do_sample=False,
            )

        # Decode both ways — use raw for tool call parsing, clean for display
        response_raw = _processor.decode(out[0][input_len:], skip_special_tokens=False)
        response_clean = _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()

        tool_call = _parse_tool_call(response_raw)
        if tool_call is None:
            print(f"[tool] final answer after {step} steps", flush=True)
            return response_clean

        tool_name = tool_call.get("name") or tool_call.get("function", {}).get("name", "")
        tool_args = tool_call.get("arguments") or tool_call.get("function", {}).get("arguments", {})
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {}

        print(f"[tool] step {step}: {tool_name}({json.dumps(tool_args)[:80]})", flush=True)
        result = execute_tool(tool_name, tool_args, workspace=workspace)
        print(f"[tool] result: {result[:100]}", flush=True)

        if step_callback:
            step_callback(step + 1, tool_name, tool_args, result)

        # Feed result back using user message format (reliable across Gemma versions)
        current_messages.append({
            "role": "model",
            "content": [{"type": "text", "text": response_clean or f"[called {tool_name}]"}]
        })
        current_messages.append({
            "role": "user",
            "content": [{"type": "text", "text": f"Tool `{tool_name}` result:\n{result}\n\nPlease provide your final answer based on the tool result above."}]
        })

    return "(max steps reached)"


def _parse_tool_call(text: str) -> "dict | None":
    """Try to extract a tool call from model output (raw, with special tokens)."""
    import json
    import re

    # Pattern 1: Gemma 4 with special tokens preserved
    # <|tool_call>call:exec_shell{command:<|"|>date<|"|>}<tool_call|>
    # The <|"|> tokens are string delimiters
    m = re.search(r'call:(\w+)\{([^}]*)\}', text)
    if m:
        tool_name = m.group(1)
        raw_args = m.group(2)
        # Remove Gemma string delimiter tokens <|"|>
        raw_args = re.sub(r'<\|"\|>', '"', raw_args)
        raw_args = raw_args.strip()
        args = {}
        # Try JSON parse of args as object
        try:
            args = json.loads('{' + raw_args + '}')
        except Exception:
            # Fall back to key:value parsing
            for pair in re.findall(r'(\w+):\s*"([^"]*)"', raw_args):
                args[pair[0]] = pair[1]
            if not args:
                for pair in re.findall(r'(\w+):\s*([^,}]+)', raw_args):
                    args[pair[0].strip()] = pair[1].strip().strip('"')
        if tool_name and args:
            return {"name": tool_name, "arguments": args}

    # Pattern 2: JSON code block
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if 'name' in data or 'function' in data:
                return data
        except Exception:
            pass

    # Pattern 3: <tool_call> tags
    m = re.search(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # Pattern 4: OpenAI-style {"name": ..., "arguments": {...}}
    m = re.search(r'(\{"name":\s*"[^"]+",\s*"arguments":\s*\{.*?\}\s*\})', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass

    # Pattern 5: Raw JSON object with name/function key
    stripped = text.strip()
    if stripped.startswith('{') and stripped.endswith('}'):
        try:
            data = json.loads(stripped)
            if 'name' in data or 'function' in data:
                return data
        except Exception:
            pass

    return None


def infer_with_image(image_path: str, prompt: str, max_new_tokens: int = 512) -> str:
    """Run multimodal inference with an image. Returns text response."""
    from PIL import Image
    import torch

    img = Image.open(image_path).convert("RGB")
    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": img},
            {"type": "text",  "text": prompt},
        ]}
    ]
    text = _processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = _processor(text=text, images=[img], return_tensors="pt").to(_model.device)
    input_len = inputs["input_ids"].shape[-1]
    with torch.no_grad():
        out = _model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    return _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()


def infer_with_audio(audio_path: str, prompt: str = "Transcribe this audio.", max_new_tokens: int = 512) -> str:
    """Run multimodal inference with audio. Returns transcription/response."""
    import torch
    import soundfile as sf
    import numpy as np

    # Load audio — convert OGG/MP3/etc to PCM via soundfile or ffmpeg fallback
    try:
        audio_array, sample_rate = sf.read(audio_path, dtype="float32")
    except Exception:
        # ffmpeg fallback for OGG (Telegram voice notes)
        import subprocess, tempfile, os
        tmp = tempfile.mktemp(suffix=".wav")
        subprocess.run(["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", tmp],
                       capture_output=True, timeout=30)
        audio_array, sample_rate = sf.read(tmp, dtype="float32")
        os.unlink(tmp)

    if audio_array.ndim > 1:
        audio_array = audio_array.mean(axis=1)  # stereo → mono

    messages = [
        {"role": "user", "content": [
            {"type": "audio", "audio": {"array": audio_array, "sampling_rate": sample_rate}},
            {"type": "text",  "text": prompt},
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
        out = _model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    return _processor.decode(out[0][input_len:], skip_special_tokens=True).strip()


def vram_free_mb() -> int:
    """Return free VRAM in MB, or RAM if CPU."""
    if torch.cuda.is_available():
        free, _ = torch.cuda.mem_get_info()
        return free // (1024 * 1024)
    import psutil
    return psutil.virtual_memory().available // (1024 * 1024)
