"""
test_skills.py — Unit tests for skills.py parsing and lookup.
No model, no HTTP — pure logic.
"""

import sys
import os
import tempfile
from pathlib import Path

# Make src importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import skills as skills_mod


VALID_SKILL_MD = """\
---
name: test-skill
description: A test skill for unit testing
commands:
  - /test
  - /demo
---
## Instructions

Do the thing.
"""

SKILL_NO_FRONTMATTER = """\
## Instructions

Just some markdown with no frontmatter.
"""

SKILL_INVALID_YAML = """\
---
name: [broken yaml
---
## Body
"""


class TestParseSkill:
    def _write_skill(self, tmp_dir, content, filename="SKILL.md"):
        p = Path(tmp_dir) / filename
        p.write_text(content)
        return p

    def test_valid_skill_parsed(self, tmp_path):
        p = self._write_skill(tmp_path, VALID_SKILL_MD)
        result = skills_mod._parse_skill(p)
        assert result is not None
        assert result["name"] == "test-skill"
        assert result["description"] == "A test skill for unit testing"
        assert "/test" in result["commands"]
        assert "Do the thing." in result["instructions"]

    def test_no_frontmatter_returns_none(self, tmp_path):
        p = self._write_skill(tmp_path, SKILL_NO_FRONTMATTER)
        result = skills_mod._parse_skill(p)
        assert result is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        p = self._write_skill(tmp_path, SKILL_INVALID_YAML)
        result = skills_mod._parse_skill(p)
        assert result is None

    def test_path_stored(self, tmp_path):
        p = self._write_skill(tmp_path, VALID_SKILL_MD)
        result = skills_mod._parse_skill(p)
        assert result["path"] == str(p)

    def test_missing_name_uses_parent_dir(self, tmp_path):
        content = "---\ndescription: no name here\n---\nbody"
        p = self._write_skill(tmp_path, content)
        result = skills_mod._parse_skill(p)
        assert result is not None
        assert result["name"] == tmp_path.name  # fallback to directory name


class TestLoadAll:
    def test_loads_from_dir(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(VALID_SKILL_MD)
        result = skills_mod.load_all(str(tmp_path))
        assert len(result) == 1
        assert result[0]["name"] == "test-skill"

    def test_ignores_invalid_skills(self, tmp_path):
        bad = tmp_path / "bad-skill"
        bad.mkdir()
        (bad / "SKILL.md").write_text(SKILL_NO_FRONTMATTER)
        result = skills_mod.load_all(str(tmp_path))
        assert len(result) == 0

    def test_loads_nested(self, tmp_path):
        nested = tmp_path / "category" / "deep-skill"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text(VALID_SKILL_MD)
        result = skills_mod.load_all(str(tmp_path))
        assert len(result) == 1

    def test_empty_dir_returns_empty(self, tmp_path):
        result = skills_mod.load_all(str(tmp_path))
        assert result == []


class TestFindSkill:
    def _make_skills(self):
        return [
            {"name": "weather", "description": "Get weather info"},
            {"name": "search", "description": "Search the web"},
            {"name": "notes", "description": "Manage notes"},
        ]

    def test_find_exact_match(self):
        skills = self._make_skills()
        result = skills_mod.find("weather", skills)
        assert result is not None
        assert result["name"] == "weather"

    def test_find_case_insensitive(self):
        skills = self._make_skills()
        assert skills_mod.find("WEATHER", skills) is not None
        assert skills_mod.find("Weather", skills) is not None

    def test_find_with_whitespace(self):
        skills = self._make_skills()
        assert skills_mod.find("  weather  ", skills) is not None

    def test_find_nonexistent_returns_none(self):
        skills = self._make_skills()
        assert skills_mod.find("nonexistent", skills) is None

    def test_find_empty_list(self):
        assert skills_mod.find("anything", []) is None
