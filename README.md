# Kernel

> A local-first AI agent powered by Gemma 4 E2B-it.  
> Zero cloud. Zero token cost. Runs on the edge.  
> Talks to you via Telegram, CLI, or API.  
> Supports multimodal input: images and voice via Gemma 4.

**Current stable: v0.9.0**

---

## Quick Start

```bash
git clone https://github.com/fabiopacifici-bot/kernel
cd kernel
cp .env.example .env  # add your MICROCLAW_TELEGRAM_BOT_TOKEN + MICROCLAW_TELEGRAM_CHAT_ID
pip install -r requirements.txt
bash start.sh
```

That's it. The agent starts on port 8769, loads the model, and connects the Telegram bot.

### Make the `kernel` CLI globally available

After cloning, run this once to create a symlink in your PATH:

```bash
ln -sf "$(pwd)/microclaw" ~/.local/bin/kernel
```

The symlink always points to the live repo — so `git pull` or `/update` automatically
picks up the latest CLI without re-running this step.

### Requirements

- Python 3.11+
- CUDA GPU with **6GB+ VRAM** (Gemma 4 E2B-it requires ~5.5GB in bfloat16; 8GB+ recommended)
- A Telegram bot token (create one via [@BotFather](https://t.me/botfather))

---

## Interfaces

### Telegram Bot

Send `/help` to your bot — you'll get a menu with tappable inline buttons for every command.

```
/help         → interactive command menu (inline buttons)
/skills       → list available skills
/routines     → list available routines
/status       → VRAM + system info
/run <name>   → execute a routine by name
/search <q>   → search ecosystem for skills/routines
/install <n>  → install a skill or routine from the ecosystem
/clone <path> → copy a skill from another agent
/verbose      → toggle verbose mode (streams tool steps live)
/replica      → manage named persistent replicas
/update       → pull latest release and restart
/restart      → restart without pulling (apply config changes)
/rollback     → revert to the previous commit
```

Tap any button from `/help` to fire that command directly — no typing needed.

### CLI

```bash
./kernel            # start chat
./kernel /skills    # list skills
./kernel /routines  # list routines
./kernel /status    # VRAM + system info
./kernel /help      # all commands
```

### API

```bash
# Chat
curl -X POST http://localhost:8769/message -H 'Content-Type: application/json' \
  -d '{"message": "hello"}'

# Slash commands also work via API
curl -X POST http://localhost:8769/message -H 'Content-Type: application/json' \
  -d '{"message": "/skills"}'

# Health check
curl http://localhost:8769/health

# Spawn a named persistent replica with brief injection
curl -X POST http://localhost:8769/replica/named \
  -H 'Content-Type: application/json' \
  -d '{"name": "lawy", "role": "custom", "brief_path": "~/.openclaw/workspace-client/projects/multistack/brief.md"}'

# Send a message to a named replica
curl -X POST http://localhost:8769/replica/lawy/message \
  -H 'Content-Type: application/json' \
  -d '{"message": "What are the IP boundaries for this meeting?"}'

# List active replicas
curl http://localhost:8769/replica/active

# Stop a named replica
curl -X DELETE http://localhost:8769/replica/lawy
```

---

## Skills & Routines

Kernel reads `SKILL.md` and `ROUTINE.md` files — the same format used by OpenClaw.
Drop any compatible skill or routine into `~/.kernel/ecosystem/` and Kernel picks it up automatically.

### Ecosystem

Three tiers:

| Tier | Location | Access |
|---|---|---|
| Community | `~/.kernel/ecosystem/community/` | `/search`, `/install` |
| Private | `~/.kernel/ecosystem/private/` | `/private_repo`, `/clone` |
| Third-party | `~/.kernel/ecosystem/third-party/` | `/clone <path>` |

```
/search ai tools          → search ecosystem
/install morning-briefing → install a skill
/clone /path/to/skill     → copy from another agent
/private_repo owner/repo  → set your private GitHub ecosystem
```

---

## Architecture

```
Telegram / CLI / API
        │
        ▼
   telegram_bot.py / kernel CLI / api.py
        │
        ▼
   agent.py — triage + tool calls
        │
   ┌────┴────┐
   │  model  │ ← Gemma 4 E2B-it (loaded once, stays in VRAM)
   └────┬────┘
        │
   skills.py + routines.py
        │
   tools.py → shell, files, web, memory
```

A single uvicorn process owns both the API server and the Telegram bot (bot runs as a daemon thread inside the startup event). `start.sh` kills port 8769 and relaunches the API — the model server is never restarted if it's already healthy.

### Named Persistent Replicas

Kernel supports spawning **named persistent replicas** — isolated agent instances that share the base model weights but each maintain their own conversation history and system prompt.

```
Kernel (main)
  ├── replica: lawy     ← legal advisor, loaded with lawy brief
  ├── replica: marty    ← marketing advisor, loaded with marty brief
  └── replica: olly     ← technical advisor, loaded with project brief
```

Key properties:
- **Shared VRAM** — all replicas use the same loaded model weights (no extra GPU memory per replica)
- **Isolated context** — each replica has its own conversation history (up to 20 turns)
- **Brief injection** — a brief file is loaded into the system prompt at spawn time
- **Persistent** — stays alive until explicitly stopped (unlike fire-and-forget task replicas)
- **VRAM cap** — max 4 replicas; each replica reserves ~512MB context budget

**API endpoints:**

| Method | Path | Description |
|---|---|---|
| `POST` | `/replica/named` | Spawn a named persistent replica |
| `POST` | `/replica/<name>/message` | Send a message, get a reply |
| `GET` | `/replica/<name>/status` | Check replica status and history length |
| `DELETE` | `/replica/<name>` | Stop and remove a replica |
| `GET` | `/replica/active` | List all active replicas |
| `POST` | `/replica/spawn` | (legacy) Spawn a fire-and-forget task replica |

**Telegram commands:**

```
/replica list           → show active replicas with status
/replica stop <name>    → stop a named replica
```

**Use case — client-facing meetings (NSA Agency):**
Spawn `kernel-lawy` and `kernel-marty` loaded with a project brief before a client meeting. Each replica participates in the Telegram group chat with scoped knowledge only. After the meeting, stop replicas and run post-meeting reconciliation.

See `workspace-client` repo and ADR-001 for the full architecture.

### Model Persistence

The model server is a **separate long-lived process** (`src/model_server.py`) that:
- Loads Gemma 4 once at startup (~30–120s on first boot)
- Survives API and bot restarts (no model reload needed)
- Exposes JSON-RPC over Unix socket (`/tmp/kernel_model.sock`)
- Refuses to start a second instance (deduplication via socket probe on startup)
- Guards against OOM: refuses to load if < 6000MB VRAM free at startup
- Reports `vram_warning: true` in `/health` if free VRAM drops below 2000MB

### VRAM Requirements

| Component | Requirement |
|---|---|
| Gemma 4 E2B-it (bfloat16) | ~5.5GB VRAM |
| Minimum to load | 6GB free at startup |
| Recommended GPU | 8GB+ VRAM |

> **Note:** `device_map="auto"` may offload layers to CPU if contiguous VRAM is fragmented.
> Inference works but is slower. Check `/tmp/kernel_model_server.log` for offloading warnings.

---

## Configuration

`config.yaml` in the repo root:

```yaml
model:
  path: google/gemma-4-E2B-it   # or local path
  device: auto                    # cuda / cpu / mps
  dtype: bfloat16

api:
  port: 8769

skills_dir: skills
routines_dir: routines
```

Environment variables (`.env`):

```
MICROCLAW_TELEGRAM_BOT_TOKEN=<your bot token>
MICROCLAW_TELEGRAM_CHAT_ID=<your chat id>   # whitelist — only this ID can talk to the bot
```

---

## Self-Update

```
/update    → checks GitHub tags, git pulls master, restarts if new code found
/restart   → restarts the process without pulling (use after config changes)
/rollback  → git reset --hard HEAD~1, then restarts
```

Update flow:
1. Fetches latest tag from GitHub Tags API (up to 100 tags, semver sorted)
2. Compares with running version
3. If newer: `git pull origin master` → reads version.py from disk → restarts via `start.sh`
4. If already on latest: confirms, no restart

---

## OpenClaw Integration

Kernel registers as a sub-agent in the OpenClaw multi-agent hierarchy:

```
Olly (main session)        ← complex reasoning, external actions
       ↕
Kernel (local agent)       ← local execution, zero token cost
```

Olly can delegate tasks to Kernel, which executes them entirely locally — no cloud tokens consumed.

---

## Ecosystem

The `~/.kernel/ecosystem/` directory holds community and private skills/routines. On startup, Kernel bootstraps from configured ecosystem repos via `git pull`.

Ecosystem repo format: a git repo with `skills/` and/or `routines/` directories, each containing standard `SKILL.md` / `ROUTINE.md` files.

Set your private ecosystem repo:
```
/private_repo fabiopacifici-bot/my-kernel-skills
```

---

## Technical Stack

| Component | Technology |
|---|---|
| Model | `google/gemma-4-E2B-it` (Gemma 4, 2.3B effective params, ~5.5GB VRAM in bfloat16) |
| Inference | `transformers` + PyTorch (CUDA preferred, CPU fallback) |
| API | FastAPI + uvicorn (port 8769) |
| Bot | python-requests long-polling (no python-telegram-bot dependency) |
| Config | YAML (`config.yaml`) |
| Skills/Routines | SKILL.md / ROUTINE.md (OpenClaw format, fully compatible) |

---

## Changelog

### v0.9.0 — May 2, 2026
- Named persistent replicas with brief injection (`spawn_named`, `/replica/named`)
- New API: `/replica/<name>/message`, `/replica/<name>/status`, `DELETE /replica/<name>`
- Updated `/replica/active` to include name, persistent flag
- Telegram `/replica list` and `/replica stop <name>` commands
- Brief file loaded into system prompt at spawn (scoped context for client meetings)
- Isolated conversation history per replica (up to 20 turns)
- Backward compat: existing `spawn(role, task)` unchanged
- VRAM cap raised to 4 replicas

### v0.8.2 — May 2, 2026
- `run_skill` and `run_routine` as callable tools — Gemma can invoke any skill or routine natively

### v0.7.0 — May 1, 2026
- Multimodal support: send images and voice notes to Kernel via Telegram
- Gemma 4 natively processes images and audio (no extra model needed)
- `infer_with_image()` and `infer_with_audio()` via model_server socket
- Fix: `tools.py` unguarded dict key access → safe `.get()` with error feedback
- Fix: `_handle_infer` type guard — string messages wrapped as user turn
- Fix: VRAM guard before model load — refuses load if < 6000MB free
- Fix: model_server dedup — stale socket detection, clean exit if already running
- Fix: `telegram_bot.py` `UnboundLocalError` for `os` in `handle_message` (inline import removed)
- Fix: API `/message` with empty body returns immediately instead of hanging on inference
- VRAM warning flag in `/health` when free VRAM < 2000MB

### v0.6.5 — April 30, 2026
- Server-side interaction logger for dataset collection
- Shared updater.py + CLI symlink install + live version in header

### v0.6.0 — April 29, 2026
- Model persistence: Gemma 4 survives API restarts via Unix socket IPC
- Separate model_server process keeps model in VRAM across bot/API restarts
- 46-test suite (unit + integration)

### v0.5.2 — April 28, 2026
- `/help` now sends inline tappable buttons for every command
- Button callbacks route directly to command handlers

### v0.5.1 — April 28, 2026
- Fix: `/update` always restarts if disk version differs from running version
- Previously "Already up to date" from git caused a false no-op even when process was outdated

### v0.5.0 — April 28, 2026
- Added `/restart` command — restarts without git pull (for config changes, recovery)
- Listed in `/help` output

### v0.4.9 — April 28, 2026
- Fetch up to 100 tags for reliable semver update check

### v0.4.8 — April 28, 2026
- Fix: sort tags by semver (not push order) to find true latest version

### v0.4.7 — April 28, 2026
- Fix: API captures all send_message replies for slash commands (not just first)

### v0.4.6 — April 28, 2026
- Fix: `/update` always does fresh tag check — no more false "nothing to update"
- Shows `v_old → v_new` on update

### v0.4.5 — April 28, 2026
- Fix: pass `ALLOWED_CHAT_ID` to `handle_message` in API slash command routing (avoids Unauthorized)

### v0.4.4 — April 28, 2026
- `/message` API now routes slash commands through `handle_message` — `/help`, `/skills`, `/routines` etc. work via API

### v0.4.3 — April 27, 2026
- Bot runs as daemon thread inside uvicorn — `start.sh` kills one process, not two
- `/update` kills bot process and restarts the single unified process

### v0.4.2 — April 27, 2026
- Fix: `/update` no longer causes downgrade bug

### v0.4.1 — April 27, 2026
- Fix: skills and routines load correctly from ecosystem

### v0.4.0 — April 26, 2026
- Ecosystem commands: `/search`, `/install`, `/clone`, `/private_repo`
- Three-tier ecosystem (community / private / third-party)

### v0.3.0 — April 26, 2026
- Olly recovery integration
- `/run` command for skills

### v0.2.1 — April 26, 2026
- Official rename from MicroClaw → Kernel (17 files updated)

### v0.2.0 — April 26, 2026
- Self-update via `/update`
- Fix `/skills` and `/routines` listing

### v0.1.0 — April 3, 2026
- First release as MicroClaw

---

## Status

✅ **Active development.** Running in production.  
**Repo:** [fabiopacifici-bot/kernel](https://github.com/fabiopacifici-bot/kernel)  
**Author:** Fabio (NSA Agency)
