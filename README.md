# Kernel

> A local-first AI agent powered by Gemma 4 E2B-it.  
> Zero cloud. Zero token cost. Runs on the edge.  
> Talks to you via Telegram, CLI, or API.

**Current stable: v0.7.0**

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
- CUDA GPU with 8GB+ VRAM recommended (CPU fallback available)
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

A single uvicorn process owns both the API server and the Telegram bot (bot runs as a daemon thread inside the startup event). `start.sh` kills port 8769 and relaunches everything.

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
| Model | `google/gemma-4-E2B-it` (Gemma 4, 2.3B effective params) |
| Inference | `transformers` + PyTorch (CUDA preferred, CPU fallback) |
| API | FastAPI + uvicorn (port 8769) |
| Bot | python-requests long-polling (no python-telegram-bot dependency) |
| Config | YAML (`config.yaml`) |
| Skills/Routines | SKILL.md / ROUTINE.md (OpenClaw format, fully compatible) |

---

## Changelog

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
**Repo:** [fabiopacifici-bot/microclaw](https://github.com/fabiopacifici-bot/microclaw)  
**Author:** Fabio (NSA Agency)
