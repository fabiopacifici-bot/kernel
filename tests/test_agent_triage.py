"""
test_agent_triage.py — Unit tests for agent.py triage logic.
Mocks model inference — tests routing decisions only, not LLM output.
"""

import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


# Patch heavy imports before importing agent
_mock_torch = MagicMock()
_mock_torch.cuda.is_available.return_value = False
_mock_torch.cuda.mem_get_info.return_value = (4096 * 1024 * 1024, 8192 * 1024 * 1024)
_mock_torch.bfloat16 = "bfloat16"

with patch.dict("sys.modules", {
    "torch": _mock_torch,
    "transformers": MagicMock(),
    "yaml": __import__("yaml"),
    "requests": MagicMock(),
}):
    import agent
    import model as kernel_model

# Provide a mock processor so infer() doesn't crash on _processor.apply_chat_template
_mock_processor = MagicMock()
_mock_processor.apply_chat_template.return_value = "<mock prompt>"
# Mock the tokenizer call: _processor(text=...) must return an object with .to()
_mock_tensor = MagicMock()
_mock_tensor.to.return_value = _mock_tensor
_mock_processor.return_value = _mock_tensor
_mock_processor.decode.return_value = "mock model response"
kernel_model._processor = _mock_processor

# Provide a mock model so generate() doesn't crash
_mock_llm = MagicMock()
_mock_llm.generate.return_value = [MagicMock()]  # list of token ids
_mock_llm.device = "cpu"
kernel_model._model = _mock_llm


def _reset_agent(skills=None, routines=None):
    agent._skills = skills or []
    agent._routines = routines or []
    agent._config = {"api": {"openclaw_endpoint": "http://localhost:18789"}}


class TestTriageSlashCommands:
    def setup_method(self):
        _reset_agent(
            skills=[
                {"name": "weather", "description": "Get weather", "commands": ["/weather"]},
                {"name": "search", "description": "Search the web", "commands": ["/search"]},
            ],
            routines=[
                {"name": "daily-digest", "description": "Daily digest", "trigger": {}, "body": ""},
            ]
        )

    def test_skills_command_lists_skills(self):
        result = agent.triage("/skills")
        assert "weather" in result
        assert "search" in result

    def test_routines_command_lists_routines(self):
        result = agent.triage("/routines")
        assert "daily-digest" in result

    def test_skills_command_empty(self):
        _reset_agent()
        result = agent.triage("/skills")
        assert "No skills" in result

    def test_routines_command_empty(self):
        _reset_agent()
        result = agent.triage("/routines")
        assert "No routines" in result

    def test_run_nonexistent_returns_not_found(self):
        result = agent.triage("/run nonexistent_xyz")
        assert "not found" in result.lower() or "no" in result.lower()

    def test_status_returns_vram_info(self):
        with patch("agent.vram_free_mb", return_value=4096), \
             patch("model.vram_free_mb", return_value=4096), \
             patch("agent.active_replicas", return_value=[]), \
             patch("agent.can_spawn", return_value=True):
            result = agent.triage("/status")
            assert "4096" in result or "VRAM" in result


class TestTriageSkillMatching:
    def setup_method(self):
        _reset_agent(
            skills=[
                {"name": "weather", "description": "Get current weather", "commands": ["/weather"], "instructions": ""},
                {"name": "investor", "description": "Portfolio management", "commands": ["/investor"], "instructions": ""},
            ],
            routines=[]
        )

    def test_skill_triggered_by_name(self):
        result = agent.triage("weather in Rome")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_skill_triggered_by_command(self):
        result = agent.triage("/investor")
        assert isinstance(result, str)

    def test_unknown_falls_through_to_inference(self):
        result = agent.triage("tell me a joke")
        assert isinstance(result, str)


class TestTriageRoutineMatching:
    def setup_method(self):
        _reset_agent(
            skills=[],
            routines=[
                {"name": "daily-digest", "description": "Portfolio digest", "trigger": {}, "body": ""},
            ]
        )

    def test_routine_triggered_by_name(self):
        result = agent.triage("daily-digest")
        assert isinstance(result, str)

    def test_run_slash_triggers_routine(self):
        result = agent.triage("/run daily-digest")
        assert isinstance(result, str)


class TestTriageStatusKeywords:
    def setup_method(self):
        _reset_agent()

    def test_vram_keyword(self):
        with patch("agent.vram_free_mb", return_value=2048), \
             patch("model.vram_free_mb", return_value=2048), \
             patch("agent.active_replicas", return_value=[]), \
             patch("agent.can_spawn", return_value=False):
            result = agent.triage("how much vram is free?")
            assert "2048" in result or "VRAM" in result

    def test_health_keyword(self):
        with patch("agent.vram_free_mb", return_value=2048), \
             patch("model.vram_free_mb", return_value=2048), \
             patch("agent.active_replicas", return_value=[]), \
             patch("agent.can_spawn", return_value=True):
            result = agent.triage("health check please")
            assert isinstance(result, str)
            assert len(result) > 0
