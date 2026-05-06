# Kernel

> A local-first AI agent powered by Gemma 4 E2B-it.  
> Zero cloud. Zero token cost. Runs on the edge.  
> Talks to you via Telegram, CLI, or API.  
> Supports multimodal input: images and voice via Gemma 4.  
> Companion base to [kernel-evolving](https://github.com/fabiopacifici-bot/kernel-evolving) — the self-evolving research sandbox.

![version](https://img.shields.io/badge/version-v1.3.0-blue)
![status](https://img.shields.io/badge/status-production-green)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

---

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/fabiopacifici-bot/kernel/main/install.sh | bash
```

The installer clones the repo, creates a venv, installs deps, and wires the `kernel` CLI to your `$PATH`.

---

## Quick Start (manual)

```bash
git clone https://github.com/fabiopacifici-bot/kernel
cd kernel
cp .env.example .env  # add your MICROCLAW_TELEGRAM_BOT_TOKEN + MICROCLAW_TELEGRAM_CHAT_ID
pip install -r requirements.txt
bash start.sh
```

The agent starts on port 8769, loads the model, and connects the Telegram bot.

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
/evolve        → delegate to kernel-evolving sandbox (if running)
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
  -d '{"name": "analyst", "role": "custom", "brief_path": "/path/to/your/brief.md"}'

# Send a message to a named replica
curl -X POST http://localhost:8769/replica/analyst/message \
  -H 'Content-Type: application/json' \
  -d '{"message": "Summarise the key points from the brief."}'

# List active replicas
curl http://localhost:8769/replica/active

# Stop a named replica
curl -X DELETE http://localhost:8769/replica/analyst
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
        │
        ▼ (optional)
   kernel-evolving (port 8770) ← peer awareness + delegation
```

A single uvicorn process owns both the API server and the Telegram bot (bot runs as a daemon thread inside the startup event). `start.sh` kills port 8769 and relaunches the API — the model server is never restarted if it's already healthy.

### Capabilities (v1.3.0)

| Capability | ADR | Description |
|---|---|---|
| Two-tier self-evolving loop | ADR-004 | Semantic match (Tier 1) → skill synthesis (Tier 2). Base delegates to kernel-evolving sandbox when evolution is needed. |
| Think-at-Rest | ADR-005 | Idle reflection using System 1/2 speculative decoding. Base surfaces reflection triggers; evolving executes them. |
| Capability verification | ADR-006 | Yes/no model inference before Tier 1 acquisition — eliminates false positives from semantic similarity alone. |
| Capability recommendation | ADR-007 | Partial match surface between ADR-006 rejection and Tier 2. Logs near-misses, suggests composable skills. |
| Peer awareness | — | Base kernel knows about `kernel-evolving` (port 8770) and can delegate evolution tasks to it via `/evolve`. |
| Semantic embedding | — | `embedding_backend=http` via ai-server — skills and tasks embedded for cosine-similarity matching without loading a second model. |

### Peer Awareness — kernel-evolving Sandbox

Kernel base is aware of its research companion:

```
Kernel base (port 8769)   ←→   kernel-evolving (port 8770)
     │                              │
     └── delegates /evolve ─────────┘
         receives reflection
         triggers back
```

When `kernel-evolving` is running, base delegates:
- `/evolve` commands — trigger evolution cycles in the sandbox
- Think-at-Rest reflection — idle gaps are analysed in the evolving tier
- Capability gaps — Tier 2 synthesis happens in the evolving sandbox; synthesised skills are returned to base

If kernel-evolving is not running, base operates normally without evolution capabilities.

### Named Persistent Replicas

Kernel supports spawning **named persistent replicas** — isolated agent instances that share the base model weights but each maintain their own conversation history and system prompt.

```
Kernel (main)
  ├── replica: researcher   ← loaded with research brief
  ├── replica: analyst      ← loaded with analysis brief
  └── replica: reporter     ← loaded with reporting brief
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

**Use case — multi-agent pipelines:**
Spawn multiple named replicas each loaded with a different brief or role. Each replica maintains its own conversation context while sharing the underlying model weights — enabling parallel specialist agents at minimal VRAM cost.

### Semantic Embedding

Kernel uses `embedding_backend=http` to route embedding calls to an external ai-server endpoint. This enables cosine-similarity skill matching without loading a second model into VRAM:

```
task → embed(task) → cosine_sim(skill_embeddings) → top-k candidates → ADR-006 verify
```

Configure in `config.yaml`:
```yaml
embedding:
  backend: http
  url: http://localhost:8780/embed
```

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

embedding:
  backend: http
  url: http://localhost:8780/embed

skills_dir: skills
routines_dir: routines
```

Environment variables (`.env`):

```
MICROCLAW_TELEGRAM_BOT_TOKEN=<your bot token>
MICROCLAW_TELEGRAM_CHAT_ID=<your chat id>   # whitelist — only this ID can talk to the bot
KERNEL_USER_NAME=YourName
KERNEL_USER_HANDLE=yourhandle
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
       ↕
kernel-evolving            ← self-evolving sandbox (optional)
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
| Embedding | HTTP backend (ai-server, configurable) |

---

## Changelog

### v1.3.0 — May 2026
- Peer awareness: base kernel detects and delegates to `kernel-evolving` (port 8770)
- Semantic embedding via `embedding_backend=http` (ai-server) — no extra VRAM
- ADR-007 (capability recommendation) surface in base: near-miss logging + composable skill hints

### v1.2.0 — May 2026
- ADR-006: capability verification — Gemma 4 yes/no inference before Tier 1 acquisition
- Eliminates false positive skill matches from semantic similarity alone
- Tier 2 synthesis now reliably triggered for genuinely unhandled tasks

### v1.1.0 — May 2026
- ADR-005: Think-at-Rest — idle reflection via System 1/2 speculative decoding
- ADR-004: Two-tier self-evolving loop — semantic match (Tier 1) → skill synthesis (Tier 2)
- Evolution API endpoints: `/evolution/state`, `/evolution/control`, `/evolution/trigger`

### v1.0.0 — May 2026
- Named persistent replicas with brief injection (`spawn_named`, `/replica/named`)
- Multimodal support: images and voice notes via Gemma 4
- Model persistence: Gemma 4 survives API restarts via Unix socket IPC
- 46-test suite (unit + integration)
- Ecosystem commands: `/search`, `/install`, `/clone`, `/private_repo`
- Three-tier ecosystem (community / private / third-party)
- `/help` with inline tappable buttons
- Self-update via `/update`, `/rollback`, `/restart`

---

## Status

✅ **Active development.** Running in production.  
**Repo:** [fabiopacifici-bot/kernel](https://github.com/fabiopacifici-bot/kernel)  
**Research companion:** [fabiopacifici-bot/kernel-evolving](https://github.com/fabiopacifici-bot/kernel-evolving)
