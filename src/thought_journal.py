"""
thought_journal.py — ADR-005 Thought Journal writer/reader.

Writes accepted thoughts to ~/.kernel/workspace/thoughts/YYYY-MM-DD.md
"""
import os
from datetime import datetime, date
from pathlib import Path

_DEFAULT_JOURNAL_DIR = os.path.expanduser("~/.kernel/workspace/thoughts")


class ThoughtJournal:
    def __init__(self, journal_dir: str | None = None):
        self.journal_dir = os.path.expanduser(journal_dir or _DEFAULT_JOURNAL_DIR)
        os.makedirs(self.journal_dir, exist_ok=True)

    def _path_for_date(self, d: date) -> str:
        return os.path.join(self.journal_dir, d.strftime("%Y-%m-%d") + ".md")

    def write(self, thought_dict: dict) -> None:
        """Append a thought entry to today's journal file."""
        now = datetime.now()
        path = self._path_for_date(now.date())
        thought = thought_dict.get("thought", "")
        category = thought_dict.get("category", "unknown")
        score = thought_dict.get("score", 0.0)
        promoted = thought_dict.get("promote", False)
        promoted_str = "yes" if promoted else "no"

        entry = (
            f"\n## {now.strftime('%H:%M')} — {category}\n"
            f"{thought}\n"
            f"Score: {score:.2f} | Promoted: {promoted_str}\n"
        )
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)

    def read_today(self) -> list:
        """Return list of thought dicts from today's journal."""
        return self.read_date(date.today())

    def read_date(self, d: date) -> list:
        """Return list of thought dicts parsed from the given date's journal."""
        path = self._path_for_date(d)
        if not os.path.exists(path):
            return []
        return self._parse_journal(path)

    def _parse_journal(self, path: str) -> list:
        """Parse a journal markdown file into a list of thought dicts."""
        thoughts = []
        with open(path, encoding="utf-8") as f:
            content = f.read()

        import re
        pattern = re.compile(
            r"^## (\d{2}:\d{2}) — (\w+)\n(.+?)\nScore: ([\d.]+) \| Promoted: (\w+)",
            re.MULTILINE | re.DOTALL,
        )
        for m in pattern.finditer(content):
            time_str, category, thought, score, promoted = m.groups()
            thoughts.append({
                "time": time_str,
                "category": category,
                "thought": thought.strip(),
                "score": float(score),
                "promote": promoted == "yes",
            })
        return thoughts

    def read_recent(self, days: int = 7) -> list:
        """Return thoughts from the last N days."""
        from datetime import timedelta
        result = []
        today = date.today()
        for i in range(days):
            d = today - timedelta(days=i)
            result.extend(self.read_date(d))
        return result
