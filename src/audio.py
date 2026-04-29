"""
audio.py — Native audio I/O via Gemma 4 E2B.
Record from mic → bytes. Speak bytes → speaker.
No Whisper. No TTS. One model.
"""
import io
import numpy as np
try:
    import sounddevice as sd
    AUDIO_AVAILABLE = True
except OSError:
    sd = None
    AUDIO_AVAILABLE = False
    print("[audio] PortAudio not found — audio disabled")

SAMPLE_RATE = 16000
CHANNELS = 1


def record(seconds=5) -> bytes:
    """Record audio from default mic. Returns raw PCM bytes."""
    print(f"[audio] Recording {seconds}s...")
    frames = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16"
    )
    sd.wait()
    return frames.tobytes()


def play(audio_bytes: bytes):
    """Play raw PCM bytes through default speaker."""
    arr = np.frombuffer(audio_bytes, dtype="int16")
    sd.play(arr, samplerate=SAMPLE_RATE)
    sd.wait()


def listen_for_wake_word(wake_word: str, on_detected: callable):
    """
    Blocking loop: record short clips, check for wake word via model.
    Calls on_detected() when triggered.
    Placeholder — implement with model.infer() once audio input is wired.
    """
    print(f"[audio] Listening for '{wake_word}'...")
    while True:
        clip = record(seconds=2)
        # TODO: pass clip to model.infer() with audio input support
        # For now: press Ctrl+C to exit
