"""Per-agent lifecycle controls: start / stop / restart / pause / resume.

Agent containers live in a restricted incus project (``user-999``) on real
installs, so every lifecycle verb must resolve the container's project and
pass ``--project`` rather than relying on the ambient project. Pause must
actually freeze the container (``incus pause``) and Resume must unfreeze it
(``incus start`` on a Frozen instance is an unfreeze), not just flip a flag.

Every incus call is faked through ``tinyagentos.containers._run``.
"""
from __future__ import annotations

import json

import pytest

from tinyagentos.config import load_config

CONTAINER = "taos-agent-test-agent"


class FakeIncus:
    """Records every incus command and answers ``incus list`` with a listing.

    ``fail_verbs`` makes the named verbs exit non-zero so error paths can be
    exercised.
    """

    def __init__(self, status: str = "Running", project: str = "user-999",
                 name: str = CONTAINER, fail_verbs: tuple[str, ...] = ()):
        self.calls: list[list[str]] = []
        self.listing = json.dumps([
            {"name": "taos-agent-other", "project": "default", "status": "Running"},
            {"name": name, "project": project, "status": status},
        ])
        self.fail_verbs = fail_verbs

    async def __call__(self, cmd, timeout=120):
        self.calls.append(list(cmd))
        if cmd[:2] == ["incus", "list"]:
            return 0, self.listing
        if len(cmd) > 1 and cmd[1] in self.fail_verbs:
            return 1, f"Error: {cmd[1]} failed"
        return 0, ""

    def verb_calls(self, verb: str) -> list[list[str]]:
        return [c for c in self.calls if len(c) > 1 and c[0] == "incus" and c[1] == verb]


@pytest.fixture
def fake_incus(monkeypatch):
    fake = FakeIncus()
    monkeypatch.setattr("tinyagentos.containers._run", fake)
    return fake


# ---------------------------------------------------------------------------
# containers package: project resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLifecycleVerbsPassProject:
    async def test_start_passes_project(self, fake_incus):
        from tinyagentos.containers import start_container

        result = await start_container(CONTAINER)
        assert result["success"] is True
        assert fake_incus.verb_calls("start") == [
            ["incus", "start", "--project", "user-999", CONTAINER]
        ]

    async def test_stop_passes_project(self, fake_incus):
        from tinyagentos.containers import stop_container

        await stop_container(CONTAINER)
        await stop_container(CONTAINER, force=True)
        assert fake_incus.verb_calls("stop") == [
            ["incus", "stop", "--project", "user-999", CONTAINER],
            ["incus", "stop", "--project", "user-999", CONTAINER, "--force"],
        ]

    async def test_restart_passes_project(self, fake_incus):
        from tinyagentos.containers import restart_container

        result = await restart_container(CONTAINER)
        assert result["success"] is True
        assert fake_incus.verb_calls("restart") == [
            ["incus", "restart", "--project", "user-999", CONTAINER]
        ]

    async def test_pause_container_freezes_in_project(self, fake_incus):
        from tinyagentos.containers import pause_container

        result = await pause_container(CONTAINER)
        assert result["success"] is True
        assert fake_incus.verb_calls("pause") == [
            ["incus", "pause", "--project", "user-999", CONTAINER]
        ]

    async def test_list_containers_sees_all_projects(self, fake_incus):
        """The Agents app reads live state from list_containers; it must see
        the user-999 container, not only the ambient project."""
        from tinyagentos.containers import list_containers

        found = await list_containers(prefix="taos-agent-")
        assert CONTAINER in {c.name for c in found}
        assert fake_incus.verb_calls("list")[0][:3] == ["incus", "list", "--all-projects"]


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPauseRoute:
    async def test_pause_freezes_and_sets_flag(self, client, fake_incus, tmp_data_dir):
        resp = await client.post("/api/agents/test-agent/pause")
        assert resp.status_code == 200, resp.text
        assert resp.json()["paused"] is True
        assert fake_incus.verb_calls("pause") == [
            ["incus", "pause", "--project", "user-999", CONTAINER]
        ]
        assert load_config(tmp_data_dir / "config.yaml").agents[0].get("paused") is True

    async def test_pause_freeze_failure_is_reported(self, client, monkeypatch):
        fake = FakeIncus(fail_verbs=("pause",))
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        resp = await client.post("/api/agents/test-agent/pause")
        assert resp.status_code == 500
        assert "error" in resp.json()

    async def test_pause_freeze_failure_leaves_agent_unpaused(
        self, client, app, monkeypatch, tmp_data_dir,
    ):
        """A failed freeze must not leave the agent flagged paused while its
        container keeps running, including the flag prepare() sets itself."""
        fake = FakeIncus(fail_verbs=("pause",))
        monkeypatch.setattr("tinyagentos.containers._run", fake)

        class FakeOrchestrator:
            async def prepare(self, scope, reason):
                # Mirrors RestartOrchestrator._prepare_agent, which marks the
                # agent paused before the freeze is attempted.
                for a in app.state.config.agents:
                    if a["name"] in scope:
                        a["paused"] = True
                return {}

        monkeypatch.setattr(app.state, "orchestrator", FakeOrchestrator(), raising=False)
        resp = await client.post("/api/agents/test-agent/pause")
        assert resp.status_code == 500
        assert "Could not freeze" in resp.json()["error"]
        assert resp.json()["paused"] is False
        assert app.state.config.agents[0].get("paused") is False
        assert load_config(tmp_data_dir / "config.yaml").agents[0].get("paused") is False

    async def test_pause_refuses_stopped_container(self, client, monkeypatch):
        fake = FakeIncus(status="Stopped")
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        resp = await client.post("/api/agents/test-agent/pause")
        assert resp.status_code == 409
        assert fake.verb_calls("pause") == []


@pytest.mark.asyncio
class TestResumeRouteUnfreezes:
    async def test_resume_unfreezes_and_clears_flag(self, client, app, monkeypatch, tmp_data_dir):
        fake = FakeIncus(status="Frozen")
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        app.state.config.agents[0]["paused"] = True

        resp = await client.post("/api/agents/test-agent/resume")
        assert resp.status_code == 200, resp.text
        assert resp.json()["paused"] is False
        assert fake.verb_calls("start") == [
            ["incus", "start", "--project", "user-999", CONTAINER]
        ]
        assert load_config(tmp_data_dir / "config.yaml").agents[0].get("paused") is False

    async def test_resume_running_container_only_clears_flag(self, client, app, fake_incus, tmp_data_dir):
        """The controller-restart path: container already running, flag stale."""
        app.state.config.agents[0]["paused"] = True
        resp = await client.post("/api/agents/test-agent/resume")
        assert resp.status_code == 200
        assert fake_incus.verb_calls("start") == []
        assert load_config(tmp_data_dir / "config.yaml").agents[0].get("paused") is False

    async def test_resume_unfreeze_failure_is_reported(self, client, app, monkeypatch):
        fake = FakeIncus(status="Frozen", fail_verbs=("start",))
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        app.state.config.agents[0]["paused"] = True
        resp = await client.post("/api/agents/test-agent/resume")
        assert resp.status_code == 500
        assert "error" in resp.json()
        assert app.state.config.agents[0]["paused"] is True


@pytest.mark.asyncio
class TestStartRestartRoutes:
    async def test_start_clears_stale_paused_flag(self, client, app, monkeypatch, tmp_data_dir):
        """Stop marks the agent paused (via prepare); Start must not leave it so."""
        fake = FakeIncus(status="Stopped")
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        app.state.config.agents[0]["paused"] = True
        resp = await client.post("/api/agents/test-agent/start")
        assert resp.status_code == 200, resp.text
        assert fake.verb_calls("start") == [
            ["incus", "start", "--project", "user-999", CONTAINER]
        ]
        assert load_config(tmp_data_dir / "config.yaml").agents[0].get("paused") is False

    async def test_start_failure_returns_error(self, client, monkeypatch):
        fake = FakeIncus(status="Stopped", fail_verbs=("start",))
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        resp = await client.post("/api/agents/test-agent/start")
        assert resp.status_code == 500
        assert "error" in resp.json()

    async def test_restart_failure_returns_error(self, client, monkeypatch):
        fake = FakeIncus(fail_verbs=("restart",))
        monkeypatch.setattr("tinyagentos.containers._run", fake)
        resp = await client.post("/api/agents/test-agent/restart")
        assert resp.status_code == 500
        assert "error" in resp.json()
