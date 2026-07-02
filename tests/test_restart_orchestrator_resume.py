"""Boot-time agent resume after a controller update/restart (#97).

The graceful-shutdown pause marks every agent paused=True; these tests pin
that the boot-time resume covers every paused agent instead of silently
stranding them:
  - an agent whose framework handled /prepare-for-shutdown itself (no
    controller-side resume note) is still resumed;
  - a hostless agent is unpaused directly (there is no /resume to call);
  - an agent that is unreachable at boot is retried in the background and
    resumed when it comes back;
  - an agent that never comes back leaves a WARNING notification, not silence.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import tinyagentos.restart_orchestrator as ro


def _app_state(tmp_path, agents):
    notifications = SimpleNamespace(add=AsyncMock())
    config = SimpleNamespace(agents=agents, config_path=tmp_path / "config.yaml")
    return SimpleNamespace(
        config=config,
        data_dir=tmp_path,
        notifications=notifications,
        _background_tasks=set(),
    )


@pytest.fixture(autouse=True)
def _no_config_write(monkeypatch):
    import tinyagentos.config as cfg

    monkeypatch.setattr(cfg, "save_config_locked", AsyncMock())


class TestResumeAgentsFromNotes:
    @pytest.mark.asyncio
    async def test_resumes_agent_without_controller_note(self, tmp_path, monkeypatch):
        """A framework that answered /prepare-for-shutdown leaves no
        controller-side note; resume must synthesize one, not skip the agent."""
        agent = {"name": "naira", "host": "10.0.0.5", "port": 8080, "paused": True}
        state = _app_state(tmp_path, [agent])
        posted = {}

        async def fake_post(host, port, note):
            posted["note"] = note
            return True

        monkeypatch.setattr(ro, "_post_resume", fake_post)
        await ro.resume_agents_from_notes(state)

        assert agent["paused"] is False
        assert posted["note"]["reason"] == "restart"
        state.notifications.add.assert_awaited()

    @pytest.mark.asyncio
    async def test_unpauses_hostless_agent_directly(self, tmp_path):
        """Hostless agents unpause without a /resume call, and any note on
        disk is preserved (nothing consumed it)."""
        agent = {"name": "wkrlan1", "host": "", "paused": True}
        state = _app_state(tmp_path, [agent])
        note_dir = tmp_path / "agent-memory" / "wkrlan1"
        note_dir.mkdir(parents=True)
        note_file = note_dir / "resume_note.json"
        note_file.write_text(json.dumps({"reason": "pause"}))

        await ro.resume_agents_from_notes(state)

        assert agent["paused"] is False
        assert note_file.exists()

    @pytest.mark.asyncio
    async def test_uses_existing_note_when_present(self, tmp_path, monkeypatch):
        agent = {"name": "a1", "host": "10.0.0.6", "port": 8080, "paused": True}
        state = _app_state(tmp_path, [agent])
        note_dir = tmp_path / "agent-memory" / "a1"
        note_dir.mkdir(parents=True)
        (note_dir / "resume_note.json").write_text(
            json.dumps({"reason": "pause", "next_step_hint": "carry on"})
        )
        posted = {}

        async def fake_post(host, port, note):
            posted["note"] = note
            return True

        monkeypatch.setattr(ro, "_post_resume", fake_post)
        await ro.resume_agents_from_notes(state)

        assert posted["note"]["next_step_hint"] == "carry on"
        assert agent["paused"] is False
        assert not (note_dir / "resume_note.json").exists()

    @pytest.mark.asyncio
    async def test_unreachable_agent_resumed_by_retry_loop(self, tmp_path, monkeypatch):
        """The agent container boots slower than the controller: the first
        attempt fails, the background retry succeeds and unpauses it."""
        agent = {"name": "slow", "host": "10.0.0.7", "port": 8080, "paused": True}
        state = _app_state(tmp_path, [agent])
        attempts = {"n": 0}

        async def flaky_post(host, port, note):
            attempts["n"] += 1
            return attempts["n"] >= 2

        monkeypatch.setattr(ro, "_post_resume", flaky_post)
        monkeypatch.setattr(ro, "_RESUME_RETRY_INTERVAL_S", 0.01)
        monkeypatch.setattr(ro, "_RESUME_RETRY_WINDOW_S", 5)

        await ro.resume_agents_from_notes(state)
        assert agent["paused"] is True  # first attempt failed
        assert state._background_tasks  # retry loop spawned

        for task in list(state._background_tasks):
            await task

        assert agent["paused"] is False
        titles = [c.kwargs["title"] for c in state.notifications.add.await_args_list]
        assert any("resumed" in t.lower() for t in titles)

    @pytest.mark.asyncio
    async def test_never_returning_agent_leaves_warning(self, tmp_path, monkeypatch):
        agent = {"name": "gone", "host": "10.0.0.8", "port": 8080, "paused": True}
        state = _app_state(tmp_path, [agent])

        async def always_fail(host, port, note):
            return False

        monkeypatch.setattr(ro, "_post_resume", always_fail)
        monkeypatch.setattr(ro, "_RESUME_RETRY_INTERVAL_S", 0.01)
        monkeypatch.setattr(ro, "_RESUME_RETRY_WINDOW_S", 0.05)

        await ro.resume_agents_from_notes(state)
        for task in list(state._background_tasks):
            await task

        assert agent["paused"] is True
        warnings = [
            c for c in state.notifications.add.await_args_list
            if c.kwargs.get("level") == "warning"
        ]
        assert warnings and "gone" in warnings[-1].kwargs["message"]

    @pytest.mark.asyncio
    async def test_no_paused_agents_is_a_noop(self, tmp_path):
        agent = {"name": "up", "host": "10.0.0.9", "paused": False}
        state = _app_state(tmp_path, [agent])

        await ro.resume_agents_from_notes(state)

        state.notifications.add.assert_not_awaited()
        assert not state._background_tasks
