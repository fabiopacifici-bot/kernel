"""
test_api.py — Integration tests for the Kernel HTTP API.

These tests run against the live Kernel server (localhost:8769).
They are stateless — no side effects, no model warm-up required beyond /health.

Run:
    pytest tests/test_api.py -v
    pytest tests/test_api.py -v -m "not inference"  # skip inference tests (slow)
"""

import pytest
import requests

BASE_URL = "http://localhost:8769"
TIMEOUT = 10


def _get(path, timeout=TIMEOUT):
    return requests.get(f"{BASE_URL}{path}", timeout=timeout)


def _post(path, payload, timeout=TIMEOUT):
    return requests.post(f"{BASE_URL}{path}", json=payload, timeout=timeout)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def require_server():
    """Fail fast if Kernel is not running."""
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        assert r.status_code == 200, "Kernel returned non-200 on /health"
    except requests.ConnectionError:
        pytest.skip("Kernel is not running at localhost:8769 — start it with ./start.sh")


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_status_ok(self):
        r = _get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_vram_field_present(self):
        data = _get("/health").json()
        assert "vram_free_mb" in data
        assert isinstance(data["vram_free_mb"], (int, float))

    def test_active_replicas_field(self):
        data = _get("/health").json()
        assert "active_replicas" in data
        assert isinstance(data["active_replicas"], int)
        assert data["active_replicas"] >= 0

    def test_skills_count_nonnegative(self):
        data = _get("/health").json()
        assert data["skills"] >= 0

    def test_routines_count_nonnegative(self):
        data = _get("/health").json()
        assert data["routines"] >= 0


# ---------------------------------------------------------------------------
# /version
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_present(self):
        r = _get("/version")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data

    def test_version_semver_format(self):
        data = _get("/version").json()
        parts = data["version"].split(".")
        assert len(parts) == 3, f"Expected semver x.y.z, got: {data['version']}"
        for part in parts:
            assert part.isdigit(), f"Non-numeric semver part: {part}"


# ---------------------------------------------------------------------------
# /skills
# ---------------------------------------------------------------------------

class TestSkills:
    def test_returns_list(self):
        r = _get("/skills")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_each_skill_has_name(self):
        data = _get("/skills").json()
        for skill in data:
            assert "name" in skill, f"Skill missing 'name': {skill}"
            assert isinstance(skill["name"], str)
            assert skill["name"].strip() != ""

    def test_each_skill_has_description(self):
        data = _get("/skills").json()
        for skill in data:
            assert "description" in skill, f"Skill missing 'description': {skill}"

    def test_skills_count_matches_health(self):
        skills = _get("/skills").json()
        health = _get("/health").json()
        assert len(skills) == health["skills"]


# ---------------------------------------------------------------------------
# /routines
# ---------------------------------------------------------------------------

class TestRoutines:
    def test_returns_list(self):
        r = _get("/routines")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_each_routine_has_name_and_trigger(self):
        data = _get("/routines").json()
        for routine in data:
            assert "name" in routine
            assert "trigger" in routine

    def test_routines_count_matches_health(self):
        routines = _get("/routines").json()
        health = _get("/health").json()
        assert len(routines) == health["routines"]


# ---------------------------------------------------------------------------
# /system
# ---------------------------------------------------------------------------

class TestSystem:
    def test_system_info_fields(self):
        r = _get("/system")
        assert r.status_code == 200
        data = r.json()
        assert "vram_free_mb" in data
        assert "can_spawn" in data
        assert "max_replicas" in data
        assert isinstance(data["max_replicas"], int)
        assert data["max_replicas"] > 0

    def test_can_spawn_is_bool(self):
        data = _get("/system").json()
        assert isinstance(data["can_spawn"], bool)


# ---------------------------------------------------------------------------
# /workspaces
# ---------------------------------------------------------------------------

class TestWorkspaces:
    def test_returns_200(self):
        r = _get("/workspaces")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /replica/active
# ---------------------------------------------------------------------------

class TestReplicaActive:
    def test_returns_list(self):
        r = _get("/replica/active")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# /message — slash command routing (no inference required)
# ---------------------------------------------------------------------------

class TestMessageSlashCommands:
    def test_skills_command(self):
        r = _post("/message", {"message": "/skills"})
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        assert isinstance(data["reply"], str)

    def test_routines_command(self):
        r = _post("/message", {"message": "/routines"})
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data

    def test_status_command(self):
        r = _post("/message", {"message": "/status"})
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        reply = data["reply"]
        assert "VRAM" in reply or "vram" in reply.lower() or "Kernel" in reply

    def test_run_nonexistent_returns_message(self):
        r = _post("/message", {"message": "/run nonexistent_routine_xyz_404"})
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        assert "not found" in data["reply"].lower() or "no" in data["reply"].lower()

    def test_message_requires_message_field(self):
        r = _post("/message", {})
        assert r.status_code == 422  # FastAPI validation error

    def test_empty_message_returns_reply(self):
        r = _post("/message", {"message": ""})
        assert r.status_code == 200
        assert "reply" in r.json()


# ---------------------------------------------------------------------------
# /replica/spawn — unit-level checks (no actual model inference)
# ---------------------------------------------------------------------------

class TestReplicaSpawn:
    def test_spawn_invalid_role_still_returns_json(self):
        """Spawn with unknown role should not 500 — it uses a fallback system prompt."""
        r = _post("/replica/spawn", {"role": "unknown_role_xyz", "task": "test task"})
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert data["status"] in ("spawned", "rejected")

    def test_spawn_requires_role_and_task(self):
        r = _post("/replica/spawn", {"role": "coder"})
        assert r.status_code == 422

        r = _post("/replica/spawn", {"task": "some task"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# /message — inference (slow, mark to skip in fast CI)
# ---------------------------------------------------------------------------

@pytest.mark.inference
class TestMessageInference:
    """
    Tests that require a warm model. Run with:
        pytest tests/test_api.py -v -m inference --timeout=120
    """

    def test_hello_returns_reply(self):
        r = _post("/message", {"message": "Hello, are you running?"}, timeout=120)
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        assert len(data["reply"]) > 0

    def test_reply_is_string(self):
        r = _post("/message", {"message": "What is 2 + 2?"}, timeout=120)
        assert r.status_code == 200
        assert isinstance(r.json()["reply"], str)


# ---------------------------------------------------------------------------
# Named persistent replicas
# ---------------------------------------------------------------------------

class TestNamedReplicas:
    def test_spawn_named_replica(self):
        resp = _post("/replica/named", {
            "name": "test-lawy",
            "role": "custom",
            "custom_prompt": "You are a legal advisor."
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "spawned"
        assert data["name"] == "test-lawy"
        assert data["persistent"] is True

    def test_spawn_named_replica_idempotent(self):
        """Spawning the same name twice returns the existing replica."""
        _post("/replica/named", {"name": "test-idem", "custom_prompt": "You are a test."})
        resp = _post("/replica/named", {"name": "test-idem", "custom_prompt": "Different prompt."})
        assert resp.status_code == 200
        assert resp.json()["status"] == "spawned"

    def test_replica_status(self):
        _post("/replica/named", {"name": "test-status-check", "custom_prompt": "You are a test replica."})
        resp = _get("/replica/test-status-check/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "test-status-check"
        assert data["persistent"] is True
        assert "history_turns" in data

    def test_replica_status_not_found(self):
        resp = _get("/replica/nonexistent-xyz-404/status")
        assert resp.status_code == 404

    def test_stop_named_replica(self):
        _post("/replica/named", {"name": "test-olly", "custom_prompt": "You are a tech advisor."})
        resp = _delete("/replica/test-olly")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

    def test_stop_nonexistent_replica(self):
        resp = _delete("/replica/does-not-exist-xyz")
        assert resp.status_code == 404

    def test_active_replicas_shows_name_and_persistent(self):
        _post("/replica/named", {"name": "test-active-check", "custom_prompt": "You are a test."})
        resp = _get("/replica/active")
        assert resp.status_code == 200
        replicas = resp.json()
        assert isinstance(replicas, list)
        for r in replicas:
            assert "name" in r
            assert "persistent" in r

    @pytest.mark.inference
    def test_message_named_replica(self):
        _post("/replica/named", {"name": "test-marty", "custom_prompt": "You are a marketing advisor."})
        resp = _post("/replica/test-marty/message", {"message": "What is the Multistack course?"})
        assert resp.status_code == 200
        assert "reply" in resp.json()


def _delete(path, timeout=TIMEOUT):
    return requests.delete(f"{BASE_URL}{path}", timeout=timeout)
