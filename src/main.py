"""
main.py — Entry point.
Loads model, starts voice loop and API server.
"""
import argparse
import uvicorn
import threading
import model, agent
from audio import listen_for_wake_word, record, play
from api import app

def voice_loop(wake_word: str):
    print(f"[main] Voice mode — wake word: '{wake_word}'")
    while True:
        clip = record(seconds=3)
        # TODO: pass audio bytes to model once audio inference is wired
        # For now: fallback to text input
        text = input("You: ").strip()
        if not text:
            continue
        reply = agent.triage(text)
        print(f"Kernel: {reply}\n")


def main():
    parser = argparse.ArgumentParser(description="Kernel")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--mode", choices=["voice", "api", "chat"], default="chat")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    model.load(args.config)
    agent.init(args.config)

    import yaml
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    port = args.port or cfg["api"]["port"]
    wake_word = cfg["audio"].get("wake_word", "hey kernel")

    if args.mode == "api":
        uvicorn.run(app, host="0.0.0.0", port=port)

    elif args.mode == "voice":
        # Run API in background, voice loop in foreground
        t = threading.Thread(target=uvicorn.run, kwargs={"app": app, "host": "0.0.0.0", "port": port}, daemon=True)
        t.start()
        voice_loop(wake_word)

    else:  # chat
        print("Kernel chat mode. Ctrl+C to exit.\n")
        while True:
            try:
                text = input("You: ").strip()
                if not text:
                    continue
                reply = agent.triage(text)
                print(f"Kernel: {reply}\n")
            except KeyboardInterrupt:
                print("\nBye.")
                break


if __name__ == "__main__":
    main()
