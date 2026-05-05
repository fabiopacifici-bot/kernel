"""
test_thought_engine.py — Tests for ADR-005 Think-at-Rest subsystem.

All model_client.infer calls are mocked — no real model needed.
"""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add src/ to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ── IdleDetector ──────────────────────────────────────────────────────────────

def test_idle_detector_fires_after_threshold():
    """IdleDetector should call the callback after threshold_s seconds."""
    from thought_engine import IdleDetector

    fired = threading.Event()

    def on_idle():
        fired.set()

    detector = IdleDetector(threshold_s=0.1, callback=on_idle)
    detector.start()
    fired.wait(timeout=2.0)
    detector.stop()

    assert fired.is_set(), "IdleDetector callback was not fired within timeout"


def test_idle_detector_resets_on_ping():
    """After ping(), the idle timer resets and callback should not fire immediately."""
    from thought_engine import IdleDetector

    fire_count = [0]

    def on_idle():
        fire_count[0] += 1

    detector = IdleDetector(threshold_s=0.3, callback=on_idle)
    detector.start()
    # Keep pinging to prevent idle
    for _ in range(5):
        time.sleep(0.1)
        detector.ping()
    detector.stop()

    assert fire_count[0] == 0, "Callback should not have fired while pings keep resetting timer"


# ── ThoughtEvaluator ──────────────────────────────────────────────────────────

def test_thought_evaluator_discards_low_score():
    """ThoughtEvaluator should discard thoughts with score < min_score."""
    from thought_engine import ThoughtEvaluator

    mock_response = json.dumps([
        {"thought": "Good thought", "score": 0.8, "category": "curiosity", "promote": True},
        {"thought": "Bad thought", "score": 0.2, "category": "retrospective", "promote": False},
        {"thought": "Borderline", "score": 0.39, "category": "self_improvement", "promote": False},
    ])

    with patch("model_client.infer", return_value=mock_response):
        evaluator = ThoughtEvaluator(min_score=0.4)
        results = evaluator.evaluate(["thought1", "thought2", "thought3"])

    assert len(results) == 1
    assert results[0]["thought"] == "Good thought"
    assert results[0]["score"] == 0.8


def test_thought_evaluator_returns_empty_on_model_error():
    """ThoughtEvaluator should return [] when model returns error."""
    from thought_engine import ThoughtEvaluator

    with patch("model_client.infer", return_value="[model_server error] connection refused"):
        evaluator = ThoughtEvaluator()
        results = evaluator.evaluate(["some thought"])

    assert results == []


def test_thought_evaluator_handles_invalid_json():
    """ThoughtEvaluator should return [] on malformed JSON."""
    from thought_engine import ThoughtEvaluator

    with patch("model_client.infer", return_value="not valid json at all"):
        evaluator = ThoughtEvaluator()
        results = evaluator.evaluate(["some thought"])

    assert results == []


def test_thought_evaluator_normalises_unknown_category():
    """Unknown categories should be normalised to 'curiosity'."""
    from thought_engine import ThoughtEvaluator

    mock_response = json.dumps([
        {"thought": "Unusual", "score": 0.7, "category": "weird_unknown_category", "promote": True},
    ])

    with patch("model_client.infer", return_value=mock_response):
        evaluator = ThoughtEvaluator(min_score=0.4)
        results = evaluator.evaluate(["unusual thought"])

    assert results[0]["category"] == "curiosity"


# ── ThoughtJournal ────────────────────────────────────────────────────────────

def test_thought_journal_write_read():
    """ThoughtJournal.write() should persist a thought readable by read_today()."""
    from thought_journal import ThoughtJournal

    with tempfile.TemporaryDirectory() as tmpdir:
        journal = ThoughtJournal(journal_dir=tmpdir)
        thought = {
            "thought": "Test thought for journal",
            "category": "curiosity",
            "score": 0.75,
            "promote": True,
        }
        journal.write(thought)

        entries = journal.read_today()
        assert len(entries) == 1
        assert entries[0]["thought"] == "Test thought for journal"
        assert entries[0]["category"] == "curiosity"
        assert abs(entries[0]["score"] - 0.75) < 0.01
        assert entries[0]["promote"] is True


def test_thought_journal_write_multiple():
    """ThoughtJournal should support multiple writes and read them all back."""
    from thought_journal import ThoughtJournal

    with tempfile.TemporaryDirectory() as tmpdir:
        journal = ThoughtJournal(journal_dir=tmpdir)
        for i in range(3):
            journal.write({
                "thought": f"Thought number {i}",
                "category": "retrospective",
                "score": 0.5 + i * 0.1,
                "promote": False,
            })

        entries = journal.read_today()
        assert len(entries) == 3
        assert entries[0]["thought"] == "Thought number 0"
        assert entries[2]["thought"] == "Thought number 2"


def test_thought_journal_empty_for_no_file():
    """read_today() should return [] when no journal file exists yet."""
    from thought_journal import ThoughtJournal

    with tempfile.TemporaryDirectory() as tmpdir:
        journal = ThoughtJournal(journal_dir=tmpdir)
        entries = journal.read_today()
        assert entries == []


# ── Promotion engine ──────────────────────────────────────────────────────────

def test_promotion_creates_idea_file():
    """ThinkAtRest should write high-score thoughts to ideas_dir."""
    import yaml
    from thought_engine import ThinkAtRest

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_dir = os.path.join(tmpdir, "thoughts")
        ideas_dir = os.path.join(tmpdir, "ideas")
        cfg = {
            "thinking": {
                "enabled": True,
                "idle_threshold_s": 9999,
                "thought_interval_s": 9999,
                "min_score": 0.4,
                "promote_threshold": 0.7,
                "proactive_telegram": False,
                "proactive_max_per_day": 2,
                "journal_dir": journal_dir,
                "ideas_dir": ideas_dir,
            }
        }
        think = ThinkAtRest(config=cfg)

        thought = {
            "thought": "This is a highly insightful reflection",
            "category": "curiosity",
            "score": 0.9,
            "promote": True,
        }
        think._on_thought_accepted(thought)

        idea_files = list(Path(ideas_dir).glob("*.md"))
        assert len(idea_files) == 1
        content = idea_files[0].read_text()
        assert "highly insightful reflection" in content


def test_promotion_self_improvement_writes_todo():
    """self_improvement thoughts should append to todos.md."""
    from thought_engine import ThinkAtRest

    with tempfile.TemporaryDirectory() as tmpdir:
        journal_dir = os.path.join(tmpdir, "thoughts")
        ideas_dir = os.path.join(tmpdir, "ideas")
        todos_path = os.path.join(tmpdir, "todos.md")

        cfg = {
            "thinking": {
                "enabled": True,
                "idle_threshold_s": 9999,
                "thought_interval_s": 9999,
                "min_score": 0.4,
                "promote_threshold": 0.9,  # high so this doesn't also trigger idea
                "proactive_telegram": False,
                "proactive_max_per_day": 2,
                "journal_dir": journal_dir,
                "ideas_dir": ideas_dir,
            }
        }
        think = ThinkAtRest(config=cfg)
        # Patch todos path
        with patch.object(think, "_add_tracker_todo") as mock_todo:
            thought = {
                "thought": "I should improve my response latency",
                "category": "self_improvement",
                "score": 0.65,
                "promote": False,
            }
            think._on_thought_accepted(thought)
            mock_todo.assert_called_once_with("I should improve my response latency")
