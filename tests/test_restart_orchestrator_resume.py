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


class TestCapContextSnapshot:
    def test_leaves_small_snapshot_untouched(self):
        note = {"context_snapshot": {"key": "value"}}
        ro._cap_context_snapshot(note)
        assert note["context_snapshot"] == {"key": "value"}

    def test_truncates_oversized_snapshot(self):
        big = {f"field_{i}": "x" * 200 for i in range(500)}
        note = {"context_snapshot": big}
        original_size = len(json.dumps(big, separators=(",", ":")))
        assert original_size > ro._MAX_CONTEXT_SNAPSHOT_BYTES
        ro._cap_context_snapshot(note)
        capped = note["context_snapshot"]
        capped_size = len(json.dumps(capped, separators=(",", ":")))
        assert capped_size <= ro._MAX_CONTEXT_SNAPSHOT_BYTES
        assert capped is not big
        assert "_truncated" in capped
        assert isinstance(capped["_truncated"], dict)
        assert "dropped_fields" in capped["_truncated"]

    def test_oversized_snapshot_keeps_required_fields(self):
        snapshot = {
            "agent_id": "a" * 100,
            "session_id": "b" * 100,
            "transcript": "x" * 60000,
            "memory": "y" * 1000,
        }
        note = {"context_snapshot": snapshot}
        ro._cap_context_snapshot(note)
        capped = note["context_snapshot"]
        assert "agent_id" in capped
        assert "session_id" in capped
        json.dumps(capped)
        assert len(json.dumps(capped, separators=(",", ":"))) <= ro._MAX_CONTEXT_SNAPSHOT_BYTES
        assert "transcript" not in capped
        assert capped["_truncated"]["dropped_fields"][0] == "transcript"

    def test_oversized_snapshot_with_long_field_names_stays_within_cap(self):
        snapshot = {"agent_id": "a" * 100}
        for i in range(200):
            snapshot[f"{'x' * 400}{i}"] = "y" * 200
        note = {"context_snapshot": snapshot}
        original_size = len(json.dumps(snapshot, separators=(",", ":")))
        assert original_size > ro._MAX_CONTEXT_SNAPSHOT_BYTES
        ro._cap_context_snapshot(note)
        capped = note["context_snapshot"]
        capped_size = len(json.dumps(capped, separators=(",", ":")))
        assert capped_size <= ro._MAX_CONTEXT_SNAPSHOT_BYTES
        assert "agent_id" in capped

    def test_required_fields_survive_long_name_fields(self):
        """The required fields must be preserved because they are required,
        not because they happen to be small. A snapshot whose agent_id and
        session_id carry MODERATE values beside many long-NAME fields with
        short values sorts the required fields FIRST under value-size
        ordering, so a cap that only sorts by size drops exactly the fields
        the contract promises to keep."""
        snapshot = {"agent_id": "a" * 500, "session_id": "b" * 500}
        for i in range(300):
            snapshot[f"{'x' * 400}{i}"] = "y" * 10
        note = {"context_snapshot": snapshot}
        original_size = len(json.dumps(snapshot, separators=(",", ":")))
        assert original_size > ro._MAX_CONTEXT_SNAPSHOT_BYTES
        ro._cap_context_snapshot(note)
        capped = note["context_snapshot"]
        assert "agent_id" in capped
        assert "session_id" in capped
        assert capped["agent_id"] == "a" * 500
        assert capped["session_id"] == "b" * 500
        # A fix that keeps the fields by abandoning the cap is not a fix.
        assert len(json.dumps(capped, separators=(",", ":"))) <= ro._MAX_CONTEXT_SNAPSHOT_BYTES

    def test_required_field_larger_than_cap_still_respects_cap(self):
        """Preservation is bounded by the cap: an agent_id that alone exceeds
        the limit must still be dropped, or the note re-triggers the very
        overflow the cap exists to prevent."""
        snapshot = {
            "agent_id": "a" * (ro._MAX_CONTEXT_SNAPSHOT_BYTES + 1000),
            "session_id": "b" * 100,
            "memory": "y" * 1000,
        }
        note = {"context_snapshot": snapshot}
        ro._cap_context_snapshot(note)
        capped = note["context_snapshot"]
        assert len(json.dumps(capped, separators=(",", ":"))) <= ro._MAX_CONTEXT_SNAPSHOT_BYTES
        assert "agent_id" not in capped
        # The smaller required field still fits, so it is still kept.
        assert capped["session_id"] == "b" * 100

    def test_empty_snapshot_is_noop(self):
        for val in [{}, None, "", "str"]:
            note = {"context_snapshot": val}
            ro._cap_context_snapshot(note)
            assert note["context_snapshot"] == val

    def test_missing_snapshot_is_noop(self):
        note = {"reason": "pause"}
        ro._cap_context_snapshot(note)
        assert "context_snapshot" not in note


class TestResumeRetryLoopCapsSnapshot:
    @pytest.mark.asyncio
    async def test_retry_loop_caps_oversized_snapshot(self, tmp_path, monkeypatch):
        """When the first resume attempt fails (agent slow to boot), the
        retry loop re-loads the note from disk and posts it again. The
        context_snapshot must still be capped on every retry, not only on
        the initial attempt."""
        agent = {"name": "slow", "host": "10.0.0.7", "port": 8080, "paused": True}
        state = _app_state(tmp_path, [agent])
        note_dir = tmp_path / "agent-memory" / "slow"
        note_dir.mkdir(parents=True)
        big_snapshot = {f"field_{i}": "x" * 200 for i in range(500)}
        (note_dir / "resume_note.json").write_text(
            json.dumps({"reason": "pause", "context_snapshot": big_snapshot})
        )

        posted_notes = []
        attempts = {"n": 0}

        async def flaky_post(host, port, note):
            attempts["n"] += 1
            posted_notes.append(dict(note))
            return attempts["n"] >= 2

        monkeypatch.setattr(ro, "_post_resume", flaky_post)
        monkeypatch.setattr(ro, "_RESUME_RETRY_INTERVAL_S", 0.01)
        monkeypatch.setattr(ro, "_RESUME_RETRY_WINDOW_S", 5)

        await ro.resume_agents_from_notes(state)

        for task in list(state._background_tasks):
            await task

        assert attempts["n"] == 2
        for posted in posted_notes:
            encoded = json.dumps(
                posted["context_snapshot"], separators=(",", ":")
            )
            assert len(encoded) <= ro._MAX_CONTEXT_SNAPSHOT_BYTES
