"""
test_routines.py — Unit tests for routines.py parsing and logic.
No model, no HTTP — pure logic.
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import routines as routines_mod


VALID_ROUTINE_MD = """\
---
name: daily-digest
description: Runs the daily portfolio digest
trigger:
  type: cron
  schedule: "0 8 * * *"
---
## Steps

Check the portfolio status.

```bash
echo "Running digest"
```
"""

ROUTINE_NO_FRONTMATTER = "## Steps\nNo frontmatter here."

ROUTINE_MINIMAL = """\
---
name: minimal
description: Minimal routine
trigger: {}
---
## Steps

Just a step.
"""


class TestParseRoutine:
    def test_valid_routine_parsed(self, tmp_path):
        p = tmp_path / "ROUTINE.md"
        p.write_text(VALID_ROUTINE_MD)
        result = routines_mod._parse_routine(p)
        assert result is not None
        assert result["name"] == "daily-digest"
        assert result["description"] == "Runs the daily portfolio digest"
        assert result["trigger"]["type"] == "cron"

    def test_no_frontmatter_returns_none(self, tmp_path):
        p = tmp_path / "ROUTINE.md"
        p.write_text(ROUTINE_NO_FRONTMATTER)
        result = routines_mod._parse_routine(p)
        assert result is None

    def test_body_captured(self, tmp_path):
        p = tmp_path / "ROUTINE.md"
        p.write_text(VALID_ROUTINE_MD)
        result = routines_mod._parse_routine(p)
        assert "Steps" in result["body"] or "digest" in result["body"]

    def test_path_stored(self, tmp_path):
        p = tmp_path / "ROUTINE.md"
        p.write_text(VALID_ROUTINE_MD)
        result = routines_mod._parse_routine(p)
        assert result["path"] == str(p)

    def test_minimal_routine(self, tmp_path):
        p = tmp_path / "ROUTINE.md"
        p.write_text(ROUTINE_MINIMAL)
        result = routines_mod._parse_routine(p)
        assert result is not None
        assert result["name"] == "minimal"


class TestLoadAll:
    def test_loads_from_dir(self, tmp_path):
        r_dir = tmp_path / "daily-digest"
        r_dir.mkdir()
        (r_dir / "ROUTINE.md").write_text(VALID_ROUTINE_MD)
        result = routines_mod.load_all(str(tmp_path))
        assert len(result) == 1
        assert result[0]["name"] == "daily-digest"

    def test_ignores_invalid(self, tmp_path):
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "ROUTINE.md").write_text(ROUTINE_NO_FRONTMATTER)
        result = routines_mod.load_all(str(tmp_path))
        assert result == []

    def test_empty_dir(self, tmp_path):
        assert routines_mod.load_all(str(tmp_path)) == []


class TestFindRoutine:
    def _make_routines(self):
        return [
            {"name": "daily-digest", "description": "Morning digest", "trigger": {}},
            {"name": "weekly-recap", "description": "Weekly summary", "trigger": {}},
        ]

    def test_find_exact(self):
        routines = self._make_routines()
        r = routines_mod.find("daily-digest", routines)
        assert r is not None
        assert r["name"] == "daily-digest"

    def test_find_case_insensitive(self):
        routines = self._make_routines()
        assert routines_mod.find("DAILY-DIGEST", routines) is not None

    def test_not_found_returns_none(self):
        routines = self._make_routines()
        assert routines_mod.find("nonexistent", routines) is None


class TestExtractShellBlocks:
    def test_extracts_bash_block(self):
        body = "Some text\n```bash\necho hello\n```\nMore text"
        result = routines_mod._extract_shell_blocks(body)
        assert len(result) == 1
        assert "echo hello" in result[0]

    def test_extracts_shell_block(self):
        body = "```shell\nls -la\n```"
        result = routines_mod._extract_shell_blocks(body)
        assert len(result) == 1
        assert "ls -la" in result[0]

    def test_multiple_blocks(self):
        body = "```bash\ncmd1\n```\ntext\n```bash\ncmd2\n```"
        result = routines_mod._extract_shell_blocks(body)
        assert len(result) == 2

    def test_no_blocks(self):
        body = "Just plain text with no code blocks."
        result = routines_mod._extract_shell_blocks(body)
        assert result == []

    def test_non_shell_blocks_ignored(self):
        body = "```python\nprint('hello')\n```"
        result = routines_mod._extract_shell_blocks(body)
        assert result == []


class TestExtractContext:
    def test_extracts_steps_section(self):
        body = "## Intro\nSome intro\n\n## Steps\nStep 1\nStep 2\n\n## End\nFoo"
        result = routines_mod._extract_context(body)
        assert "Step 1" in result
        assert "Step 2" in result

    def test_strips_code_blocks(self):
        body = "## Steps\nDo this\n```bash\necho hi\n```\nThen that"
        result = routines_mod._extract_context(body)
        assert "echo hi" not in result
        assert "Do this" in result

    def test_fallback_when_no_steps_section(self):
        body = "Just some body text with no Steps header."
        result = routines_mod._extract_context(body)
        assert "body text" in result
