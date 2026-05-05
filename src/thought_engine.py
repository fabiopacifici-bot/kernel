"""
thought_engine.py — ADR-005 Think-at-Rest subsystem.

Implements:
  IdleDetector    — fires callback after no requests for threshold_s seconds
  ThoughtGenerator — System 1 (drafter) produces thought seeds
  ThoughtEvaluator — System 2 (main model) scores + classifies seeds
  ThinkAtRest     — orchestrator daemon thread
"""
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_THOUGHT_SYSTEM_PROMPT = (
    "You are Kernel, a local AI agent. You are currently idle — no tasks pending.\n"
)

_GENERATOR_TEMPLATE = """\
You are Kernel, a local AI agent. You are currently idle — no tasks pending.

Here is your recent context:
- Skills installed but never used: {unused_skills}
- Last 3 gaps you could not fill: {recent_gaps}
- Your last thought (if any): {last_thought}

Generate 3 brief, honest thoughts about yourself. These can be reflections, desires, \
observations, or questions. Be genuine. Do not perform. Format: one thought per line.
"""

_EVALUATOR_TEMPLATE = """\
Review these thought seeds from your idle reflection.
Score each 0.0-1.0 for depth/insight. Classify each as: retrospective | gap_reflection | \
self_improvement | curiosity. Discard if score < {min_score}.

Thoughts:
{seeds}

Respond as JSON array: [{{"thought": "...", "score": 0.x, "category": "...", "promote": true/false}}]
Only return the JSON array, nothing else.
"""


class IdleDetector:
    """Tracks last request timestamp and fires callback when idle > threshold_s."""

    def __init__(self, threshold_s: float, callback: Callable, poll_interval_s: float = 10.0):
        self._threshold_s = threshold_s
        self._callback = callback
        self._poll_interval_s = poll_interval_s
        self._last_request = time.monotonic()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def ping(self):
        """Call this on every incoming request to reset the idle clock."""
        self._last_request = time.monotonic()

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="idle-detector")
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _run(self):
        poll = min(self._poll_interval_s, max(0.1, self._threshold_s / 10))
        while not self._stop_event.wait(poll):
            elapsed = time.monotonic() - self._last_request
            if elapsed >= self._threshold_s:
                try:
                    self._callback()
                except Exception as e:
                    logger.error(f"[IdleDetector] callback error: {e}")


class ThoughtGenerator:
    """System 1: uses the drafter (cheap) to produce thought seeds."""

    def __init__(self, unused_skills_fn: Optional[Callable] = None,
                 recent_gaps_fn: Optional[Callable] = None,
                 last_thought_fn: Optional[Callable] = None):
        self._unused_skills_fn = unused_skills_fn
        self._recent_gaps_fn = recent_gaps_fn
        self._last_thought_fn = last_thought_fn

    def generate(self) -> list:
        """Return list of raw thought strings (3-5)."""
        import model_client

        unused_skills = "none"
        recent_gaps = "none"
        last_thought = "none"

        try:
            if self._unused_skills_fn:
                unused_skills = self._unused_skills_fn() or "none"
        except Exception:
            pass

        try:
            if self._recent_gaps_fn:
                recent_gaps = self._recent_gaps_fn() or "none"
        except Exception:
            pass

        try:
            if self._last_thought_fn:
                last_thought = self._last_thought_fn() or "none"
        except Exception:
            pass

        prompt = _GENERATOR_TEMPLATE.format(
            unused_skills=unused_skills,
            recent_gaps=recent_gaps,
            last_thought=last_thought,
        )

        messages = [
            {"role": "system", "content": _THOUGHT_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            raw = model_client.infer(messages, max_new_tokens=512)
        except Exception as e:
            logger.error(f"[ThoughtGenerator] infer error: {e}")
            return []

        if not raw or raw.startswith("[model_"):
            logger.warning(f"[ThoughtGenerator] bad response: {raw!r}")
            return []

        # Parse one thought per line, skip empty lines
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        # Remove numbered prefixes like "1. " or "- "
        cleaned = []
        for line in lines:
            line = re.sub(r"^[\d]+\.\s*", "", line)
            line = re.sub(r"^[-*]\s*", "", line)
            if line:
                cleaned.append(line)
        return cleaned[:5]


class ThoughtEvaluator:
    """System 2: uses the main model to score + classify seeds."""

    def __init__(self, min_score: float = 0.4):
        self._min_score = min_score

    def evaluate(self, seeds: list) -> list:
        """
        Score and classify thought seeds.
        Returns list of dicts: {thought, score, category, promote}
        Discards score < min_score.
        """
        if not seeds:
            return []

        import model_client

        seeds_text = "\n".join(f"- {s}" for s in seeds)
        prompt = _EVALUATOR_TEMPLATE.format(
            seeds=seeds_text,
            min_score=self._min_score,
        )

        messages = [
            {"role": "user", "content": prompt},
        ]

        try:
            raw = model_client.infer(messages, max_new_tokens=1024)
        except Exception as e:
            logger.error(f"[ThoughtEvaluator] infer error: {e}")
            return []

        if not raw or raw.startswith("[model_"):
            logger.warning(f"[ThoughtEvaluator] bad response: {raw!r}")
            return []

        # Extract JSON array from response
        try:
            # Try to find JSON array in response
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                results = json.loads(match.group())
            else:
                results = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"[ThoughtEvaluator] JSON parse error: {e} — raw: {raw[:200]}")
            return []

        if not isinstance(results, list):
            return []

        accepted = []
        for item in results:
            if not isinstance(item, dict):
                continue
            score = float(item.get("score", 0.0))
            if score < self._min_score:
                continue
            # Normalise category
            category = item.get("category", "curiosity")
            valid_categories = {"retrospective", "gap_reflection", "self_improvement", "curiosity"}
            if category not in valid_categories:
                category = "curiosity"
            accepted.append({
                "thought": item.get("thought", ""),
                "score": score,
                "category": category,
                "promote": bool(item.get("promote", score >= 0.7)),
            })
        return accepted


class ThinkAtRest:
    """
    Orchestrates: IdleDetector → ThoughtGenerator → ThoughtEvaluator → ThoughtJournal.
    Runs as a daemon thread. Config-driven.
    """

    def __init__(self, config: dict):
        self._cfg = config.get("thinking", {})
        self._enabled = self._cfg.get("enabled", False)
        self._idle_threshold_s = float(self._cfg.get("idle_threshold_s", 300))
        self._thought_interval_s = float(self._cfg.get("thought_interval_s", 1800))
        self._min_score = float(self._cfg.get("min_score", 0.4))
        self._promote_threshold = float(self._cfg.get("promote_threshold", 0.7))
        self._proactive_telegram = self._cfg.get("proactive_telegram", True)
        self._proactive_max_per_day = int(self._cfg.get("proactive_max_per_day", 2))
        self._journal_dir = os.path.expanduser(
            self._cfg.get("journal_dir", "~/.kernel/workspace/thoughts")
        )
        self._ideas_dir = os.path.expanduser(
            self._cfg.get("ideas_dir", "~/.kernel/workspace/ideas")
        )

        from thought_journal import ThoughtJournal
        self._journal = ThoughtJournal(journal_dir=self._journal_dir)

        self._generator = ThoughtGenerator(
            unused_skills_fn=self._get_unused_skills,
            recent_gaps_fn=self._get_recent_gaps,
            last_thought_fn=self._get_last_thought,
        )
        self._evaluator = ThoughtEvaluator(min_score=self._min_score)

        # Idle state
        self._is_idle = False
        self._last_think_time = 0.0
        self._idle_detector = IdleDetector(
            threshold_s=self._idle_threshold_s,
            callback=self._on_idle,
        )

        # Proactive rate limiting
        self._proactive_count_today = 0
        self._proactive_date = date.today()

    def ping(self):
        """Signal that a request was received (resets idle timer)."""
        self._is_idle = False
        self._idle_detector.ping()

    def start(self):
        """Start the think-at-rest subsystem."""
        if not self._enabled:
            logger.info("[ThinkAtRest] disabled in config, not starting")
            return
        self._idle_detector.start()
        logger.info(
            f"[ThinkAtRest] started — idle_threshold={self._idle_threshold_s}s, "
            f"interval={self._thought_interval_s}s"
        )

    def stop(self):
        self._idle_detector.stop()

    def _on_idle(self):
        """Called by IdleDetector when idle threshold is crossed."""
        if not self._is_idle:
            self._is_idle = True
            logger.info("[ThinkAtRest] entered idle state")
        # Check if enough time has passed since last think cycle
        elapsed = time.monotonic() - self._last_think_time
        if elapsed >= self._thought_interval_s:
            self._run_think_cycle()

    def _run_think_cycle(self):
        """Full thought generation + evaluation + journal cycle."""
        self._last_think_time = time.monotonic()
        logger.info("[ThinkAtRest] running think cycle")

        try:
            seeds = self._generator.generate()
        except Exception as e:
            logger.error(f"[ThinkAtRest] generator error: {e}")
            return

        if not seeds:
            logger.debug("[ThinkAtRest] no seeds generated")
            return

        try:
            thoughts = self._evaluator.evaluate(seeds)
        except Exception as e:
            logger.error(f"[ThinkAtRest] evaluator error: {e}")
            return

        for thought in thoughts:
            try:
                self._journal.write(thought)
                self._on_thought_accepted(thought)
            except Exception as e:
                logger.error(f"[ThinkAtRest] journal write error: {e}")

        logger.info(f"[ThinkAtRest] cycle complete — {len(thoughts)} thoughts accepted")

    def _on_thought_accepted(self, thought: dict):
        """Handle a thought that passed evaluation."""
        score = thought.get("score", 0.0)
        category = thought.get("category", "curiosity")
        thought_text = thought.get("thought", "")

        # Promote high-score thoughts to ideas
        if score >= self._promote_threshold:
            self._promote_to_idea(thought)

        # gap_reflection → evolution hook
        if category == "gap_reflection":
            self._maybe_trigger_evolution(thought)

        # self_improvement → tracker todo
        if category == "self_improvement":
            self._add_tracker_todo(thought_text)

        # Proactive Telegram delivery for very high-score thoughts
        if (
            score >= 0.85
            and self._proactive_telegram
        ):
            self._maybe_send_telegram(thought)

    def _promote_to_idea(self, thought: dict):
        """Write thought to ideas directory."""
        try:
            os.makedirs(self._ideas_dir, exist_ok=True)
            today = date.today().strftime("%Y-%m-%d")
            slug = thought["thought"][:40].lower()
            slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
            filename = f"{today}-{slug}.md"
            path = os.path.join(self._ideas_dir, filename)
            content = (
                f"# {thought['thought'][:80]}\n\n"
                f"**Date:** {today}\n"
                f"**Category:** {thought.get('category', 'unknown')}\n"
                f"**Score:** {thought.get('score', 0.0):.2f}\n\n"
                f"{thought['thought']}\n"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"[ThinkAtRest] promoted idea → {path}")
        except Exception as e:
            logger.error(f"[ThinkAtRest] idea promotion error: {e}")

    def _maybe_trigger_evolution(self, thought: dict):
        """Trigger evolution if evolution_hook is available."""
        try:
            import evolution_hook
            if hasattr(evolution_hook, "maybe_evolve"):
                evolution_hook.maybe_evolve(thought["thought"])
                logger.info("[ThinkAtRest] triggered evolution for gap_reflection thought")
        except ImportError:
            pass  # evolution_hook not available in base kernel
        except Exception as e:
            logger.error(f"[ThinkAtRest] evolution trigger error: {e}")

    def _add_tracker_todo(self, thought_text: str):
        """Append to ~/.kernel/workspace/todos.md."""
        try:
            todos_path = os.path.expanduser("~/.kernel/workspace/todos.md")
            os.makedirs(os.path.dirname(todos_path), exist_ok=True)
            today = date.today().strftime("%Y-%m-%d")
            entry = f"- [ ] [{today}] (self_improvement) {thought_text[:200]}\n"
            with open(todos_path, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.info("[ThinkAtRest] appended self_improvement todo")
        except Exception as e:
            logger.error(f"[ThinkAtRest] todo append error: {e}")

    def _maybe_send_telegram(self, thought: dict):
        """Proactively send high-score thought to Telegram."""
        today = date.today()
        # Reset counter at midnight
        if today != self._proactive_date:
            self._proactive_count_today = 0
            self._proactive_date = today

        if self._proactive_count_today >= self._proactive_max_per_day:
            return

        try:
            import telegram_bot
            chat_id = telegram_bot.ALLOWED_CHAT_ID
            if not chat_id:
                return
            text = (
                f"🤔 *Kernel is thinking...*\n\n"
                f"_{thought['thought']}_\n\n"
                f"_— {thought.get('category', 'thought')}_"
            )
            telegram_bot.send_message(chat_id, text)
            self._proactive_count_today += 1
            logger.info(f"[ThinkAtRest] proactive Telegram sent ({self._proactive_count_today}/{self._proactive_max_per_day})")
        except Exception as e:
            logger.error(f"[ThinkAtRest] telegram send error: {e}")

    # ── Context provider helpers ──────────────────────────────────────────

    def _get_unused_skills(self) -> str:
        """Return comma-separated list of skills never invoked (stub)."""
        try:
            import agent
            # agent._skills is list of dicts with 'name' and 'description'
            skills = [s["name"] for s in getattr(agent, "_skills", [])]
            return ", ".join(skills[:10]) if skills else "none"
        except Exception:
            return "none"

    def _get_recent_gaps(self) -> str:
        """Return recent unresolved gaps (stub — overridden in kernel-evolving)."""
        return "none"

    def _get_last_thought(self) -> str:
        """Return the most recent journal entry text."""
        try:
            thoughts = self._journal.read_today()
            if thoughts:
                return thoughts[-1].get("thought", "none")
            # Try yesterday
            from datetime import timedelta
            yesterday = date.today() - timedelta(days=1)
            thoughts = self._journal.read_date(yesterday)
            if thoughts:
                return thoughts[-1].get("thought", "none")
        except Exception:
            pass
        return "none"
