"""Endpoint tests for tinyagentos/routes/framework.py."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from tinyagentos.frameworks import FRAMEWORKS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNTER = [0]


def _make_agent(name=None, framework="openclaw", **overrides):
    _COUNTER[0] += 1
    if name is None:
        name = f"fw-test-agent-{_COUNTER[0]}"
    agent = {
        "name": name,
        "host": "127.0.0.1",
        "qmd_index": "test",
        "color": "#abc",
        "framework": framework,
        "framework_version_tag": "v1.0.0",
        "framework_version_sha": "sha-old",
        "framework_update_status": "idle",
        "framework_update_started_at": None,
        "framework_update_last_error": None,
        "framework_last_snapshot": None,
    }
    agent.update(overrides)
    return agent


def _fw_cache(tag="v2.0.0", sha="sha-new"):
    return {
        "openclaw": {
            "tag": tag,
            "sha": sha,
            "published_at": "2026-01-01T00:00:00Z",
        }
    }


# ---------------------------------------------------------------------------
# GET /api/agents/{slug}/framework
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetAgentFramework:
    async def test_returns_framework_info(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        r = await client.get(f"/api/agents/{agent['name']}/framework")
        assert r.status_code == 200
        data = r.json()
        assert data["framework"] == "openclaw"
        assert data["installed"]["tag"] == "v1.0.0"
        assert data["installed"]["sha"] == "sha-old"
        assert data["latest"]["tag"] == "v2.0.0"
        assert data["update_available"] is True
        assert data["update_status"] == "idle"

    async def test_no_update_when_sha_matches(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(framework_version_sha="sha-new")
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache(sha="sha-new")
        r = await client.get(f"/api/agents/{agent['name']}/framework")
        assert r.status_code == 200
        data = r.json()
        assert data["update_available"] is False

    async def test_no_latest_release(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = {}
        r = await client.get(f"/api/agents/{agent['name']}/framework")
        assert r.status_code == 200
        data = r.json()
        assert data["latest"] is None
        assert data["update_available"] is False

    async def test_agent_not_found(self, client):
        r = await client.get("/api/agents/does-not-exist/framework")
        assert r.status_code == 404
        assert "error" in r.json()

    async def test_latest_framework_versions_missing_attr(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        if hasattr(app.state, "latest_framework_versions"):
            app.state.latest_framework_versions = None
        r = await client.get(f"/api/agents/{agent['name']}/framework")
        assert r.status_code == 200
        data = r.json()
        assert data["latest"] is None

    async def test_update_status_reflects_agent_state(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(
            framework_update_status="updating",
            framework_update_started_at=1234567890,
        )
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        r = await client.get(f"/api/agents/{agent['name']}/framework")
        assert r.status_code == 200
        data = r.json()
        assert data["update_status"] == "updating"
        assert data["update_started_at"] == 1234567890

    async def test_last_error_included(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(
            framework_update_status="failed",
            framework_update_last_error="install script rc=1",
        )
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        r = await client.get(f"/api/agents/{agent['name']}/framework")
        assert r.status_code == 200
        data = r.json()
        assert data["last_error"] == "install script rc=1"


# ---------------------------------------------------------------------------
# POST /api/agents/{slug}/framework/update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPostUpdate:
    async def test_accepted(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        manifest = dict(FRAMEWORKS["openclaw"])
        manifest["release_source"] = "github"
        with patch("tinyagentos.routes.framework.FRAMEWORKS", {**FRAMEWORKS, "openclaw": manifest}):
            with patch("tinyagentos.framework_update.start_update", new_callable=AsyncMock):
                r = await client.post(f"/api/agents/{agent['name']}/framework/update", json={})
        assert r.status_code == 202
        data = r.json()
        assert data["status"] == "accepted"
        assert data["update_status"] == "updating"

    async def test_agent_not_found(self, client):
        r = await client.post("/api/agents/does-not-exist/framework/update", json={})
        assert r.status_code == 404

    async def test_already_updating(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(framework_update_status="updating")
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        r = await client.post(f"/api/agents/{agent['name']}/framework/update", json={})
        assert r.status_code == 409
        assert "error" in r.json()

    async def test_failed_state_also_blocked(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(framework_update_status="failed")
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        r = await client.post(f"/api/agents/{agent['name']}/framework/update", json={})
        assert r.status_code == 409

    async def test_no_release_source(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(framework="generic")
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache()
        r = await client.post(f"/api/agents/{agent['name']}/framework/update", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_no_latest_cached_release(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = {}
        manifest = dict(FRAMEWORKS["openclaw"])
        manifest["release_source"] = "github"
        with patch("tinyagentos.routes.framework.FRAMEWORKS", {**FRAMEWORKS, "openclaw": manifest}):
            r = await client.post(f"/api/agents/{agent['name']}/framework/update", json={})
        assert r.status_code == 409
        assert "error" in r.json()

    async def test_target_version_mismatch(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache(tag="v2.0.0")
        manifest = dict(FRAMEWORKS["openclaw"])
        manifest["release_source"] = "github"
        with patch("tinyagentos.routes.framework.FRAMEWORKS", {**FRAMEWORKS, "openclaw": manifest}):
            r = await client.post(
                f"/api/agents/{agent['name']}/framework/update",
                json={"target_version": "v3.0.0"},
            )
        assert r.status_code == 400
        assert "error" in r.json()

    async def test_target_version_matches(self, client, app):
        app.state.config.agents = []
        agent = _make_agent()
        app.state.config.agents.append(agent)
        app.state.latest_framework_versions = _fw_cache(tag="v2.0.0")
        manifest = dict(FRAMEWORKS["openclaw"])
        manifest["release_source"] = "github"
        with patch("tinyagentos.routes.framework.FRAMEWORKS", {**FRAMEWORKS, "openclaw": manifest}):
            with patch("tinyagentos.framework_update.start_update", new_callable=AsyncMock):
                r = await client.post(
                    f"/api/agents/{agent['name']}/framework/update",
                    json={"target_version": "v2.0.0"},
                )
        assert r.status_code == 202


# ---------------------------------------------------------------------------
# GET /api/frameworks/latest
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetLatest:
    async def test_returns_cached_versions(self, client, app):
        app.state.latest_framework_versions = _fw_cache()
        r = await client.get("/api/frameworks/latest")
        assert r.status_code == 200
        data = r.json()
        assert "openclaw" in data
        assert data["openclaw"]["tag"] == "v2.0.0"

    async def test_refresh_triggers_poll(self, client, app):
        app.state.latest_framework_versions = {}
        app.state.http_client = AsyncMock()
        with patch("tinyagentos.auto_update.poll_frameworks", new_callable=AsyncMock) as mock_poll:
            r = await client.get("/api/frameworks/latest?refresh=true")
        assert r.status_code == 200
        mock_poll.assert_awaited_once()

    async def test_no_refresh_returns_cached(self, client, app):
        app.state.latest_framework_versions = _fw_cache()
        with patch("tinyagentos.auto_update.poll_frameworks", new_callable=AsyncMock) as mock_poll:
            r = await client.get("/api/frameworks/latest")
        assert r.status_code == 200
        mock_poll.assert_not_awaited()


# ---------------------------------------------------------------------------
# GET /api/frameworks/slash-commands
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSlashCommandsManifest:
    async def test_returns_commands_for_agents(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(name="my-agent", framework="openclaw")
        app.state.config.agents.append(agent)
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        data = r.json()
        assert "my-agent" in data
        names = [c["name"] for c in data["my-agent"]]
        assert "help" in names
        assert "clear" in names

    async def test_unknown_framework_returns_empty(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(name="orphan", framework="nonexistent-fw")
        app.state.config.agents.append(agent)
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        data = r.json()
        assert data["orphan"] == []

    async def test_no_agents(self, client, app):
        app.state.config.agents = []
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        assert r.json() == {}

    async def test_agent_without_name_skipped(self, client, app):
        app.state.config.agents = []
        agent = {"framework": "openclaw", "host": "127.0.0.1"}
        app.state.config.agents.append(agent)
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        data = r.json()
        assert agent.get("name") not in data

    async def test_framework_without_slash_commands(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(name="plain", framework="generic")
        app.state.config.agents.append(agent)
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        data = r.json()
        # generic framework now includes /help as a built-in command.
        assert len(data["plain"]) >= 1
        assert any(c["name"] == "help" for c in data["plain"])

    async def test_command_structure(self, client, app):
        app.state.config.agents = []
        agent = _make_agent(name="cmd-agent", framework="openclaw")
        app.state.config.agents.append(agent)
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        cmds = r.json()["cmd-agent"]
        for cmd in cmds:
            assert "name" in cmd
            assert "description" in cmd
