"""
test_tools_skill_routine.py — Unit tests for run_skill and run_routine tools in execute_tool().
Tests missing-args and unknown-name error paths. No model loaded, no HTTP.
"""

import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

# Make src importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import tools as tools_mod


class TestRunSkillTool:
    def test_missing_args_returns_error(self):
        """execute_tool('run_skill', {}) should return an error about missing skill_name."""
        result = tools_mod.execute_tool("run_skill", {})
        assert "error" in result.lower()
        assert "skill_name" in result

    def test_missing_input_returns_error(self):
        """execute_tool('run_skill', {'skill_name': 'foo'}) should return error about missing input."""
        result = tools_mod.execute_tool("run_skill", {"skill_name": "foo"})
        assert "error" in result.lower()
        assert "input" in result

    def test_unknown_skill_returns_not_found(self, tmp_path):
        """execute_tool with a nonexistent skill name returns 'not found' error."""
        fake_config = {"skills_dir": str(tmp_path), "routines_dir": str(tmp_path)}

        with patch("builtins.open", MagicMock(read=MagicMock(return_value=""))):
            # Patch yaml.safe_load and load_all / find / infer inside execute_tool
            with patch.dict("sys.modules", {
                "yaml": MagicMock(safe_load=MagicMock(return_value=fake_config)),
                "skills": MagicMock(
                    load_all=MagicMock(return_value=[]),
                    find=MagicMock(return_value=None),
                    run=MagicMock(return_value="ok"),
                ),
                "model": MagicMock(infer=MagicMock(return_value="ok")),
            }):
                result = tools_mod.execute_tool("run_skill", {"skill_name": "nonexistent", "input": "test"})

        assert "not found" in result.lower() or "error" in result.lower()

    def test_known_skill_executes(self, tmp_path):
        """execute_tool with a known skill calls skills.run() and returns its result."""
        fake_config = {"skills_dir": str(tmp_path)}
        fake_skill = {"name": "test-skill", "description": "Test"}

        mock_skills = MagicMock()
        mock_skills.load_all.return_value = [fake_skill]
        mock_skills.find.return_value = fake_skill
        mock_skills.run.return_value = "skill output"

        mock_model = MagicMock()
        mock_model.infer.return_value = "ok"

        with patch.dict("sys.modules", {
            "yaml": MagicMock(safe_load=MagicMock(return_value=fake_config)),
            "skills": mock_skills,
            "model": mock_model,
        }):
            result = tools_mod.execute_tool("run_skill", {"skill_name": "test-skill", "input": "do stuff"})

        assert result == "skill output"
        mock_skills.run.assert_called_once_with(fake_skill, "do stuff", mock_model.infer)


class TestRunRoutineTool:
    def test_missing_args_returns_error(self):
        """execute_tool('run_routine', {}) should return an error about missing routine_name."""
        result = tools_mod.execute_tool("run_routine", {})
        assert "error" in result.lower()
        assert "routine_name" in result

    def test_unknown_routine_returns_not_found(self, tmp_path):
        """execute_tool with a nonexistent routine name returns 'not found' error."""
        fake_config = {"routines_dir": str(tmp_path)}

        with patch.dict("sys.modules", {
            "yaml": MagicMock(safe_load=MagicMock(return_value=fake_config)),
            "routines": MagicMock(
                load_all=MagicMock(return_value=[]),
                find=MagicMock(return_value=None),
                run=MagicMock(return_value="ok"),
            ),
            "model": MagicMock(infer=MagicMock(return_value="ok")),
        }):
            result = tools_mod.execute_tool("run_routine", {"routine_name": "nonexistent"})

        assert "not found" in result.lower() or "error" in result.lower()

    def test_known_routine_executes(self, tmp_path):
        """execute_tool with a known routine calls routines.run() and returns its result."""
        fake_config = {"routines_dir": str(tmp_path)}
        fake_routine = {"name": "test-routine", "description": "Test"}

        mock_routines = MagicMock()
        mock_routines.load_all.return_value = [fake_routine]
        mock_routines.find.return_value = fake_routine
        mock_routines.run.return_value = "routine output"

        mock_model = MagicMock()
        mock_model.infer.return_value = "ok"

        with patch.dict("sys.modules", {
            "yaml": MagicMock(safe_load=MagicMock(return_value=fake_config)),
            "routines": mock_routines,
            "model": mock_model,
        }):
            result = tools_mod.execute_tool("run_routine", {"routine_name": "test-routine"})

        assert result == "routine output"
        mock_routines.run.assert_called_once_with(fake_routine, mock_model.infer)


class TestToolsListContainsNewTools:
    """Verify the TOOLS list includes the new entries."""

    def test_run_skill_in_tools_list(self):
        names = [t["function"]["name"] for t in tools_mod.TOOLS]
        assert "run_skill" in names

    def test_run_routine_in_tools_list(self):
        names = [t["function"]["name"] for t in tools_mod.TOOLS]
        assert "run_routine" in names

    def test_run_skill_has_required_params(self):
        skill_tool = next(t for t in tools_mod.TOOLS if t["function"]["name"] == "run_skill")
        params = skill_tool["function"]["parameters"]["properties"]
        assert "skill_name" in params
        assert "input" in params
        required = skill_tool["function"]["parameters"]["required"]
        assert "skill_name" in required
        assert "input" in required

    def test_run_routine_has_required_params(self):
        routine_tool = next(t for t in tools_mod.TOOLS if t["function"]["name"] == "run_routine")
        params = routine_tool["function"]["parameters"]["properties"]
        assert "routine_name" in params
        required = routine_tool["function"]["parameters"]["required"]
        assert "routine_name" in required
