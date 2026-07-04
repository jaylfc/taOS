from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.projects.routine_runner import (
    _create_task_and_wake,
    _resolve_assignee_agent,
    fire_routine,
    routine_tick_loop,
)
from tinyagentos.projects.routines_store import RoutineStore


def _make_state(**overrides) -> SimpleNamespace:
    routine_store = MagicMock()
    routine_store.record_fire = AsyncMock(return_value=None)
    routine_store.list_due = AsyncMock(return_value=[])
    routine_store.claim_due = AsyncMock(return_value=True)

    task_store = MagicMock()
    task_store.create_task = AsyncMock(
        return_value={"id": "tsk-new1", "project_id": "prj-1", "title": "Fired task"}
    )

    project_store = MagicMock()
    project_store.list_members = AsyncMock(return_value=[])
    project_store.get_project = AsyncMock(return_value={"id": "prj-1", "slug": "demo", "created_by": "u"})
    project_store.list_projects = AsyncMock(return_value=[])

    chat_channels = MagicMock()
    chat_channels.list_channels = AsyncMock(return_value=[])
    chat_channels.create_channel = AsyncMock(return_value={"id": "chan-a2a", "members": []})

    chat_messages = MagicMock()
    chat_messages.send_message = AsyncMock(return_value={"id": "msg-1"})

    bridge_sessions = MagicMock()
    bridge_sessions.enqueue_user_message = AsyncMock()

    state = SimpleNamespace(
        routine_store=overrides.get("routine_store", routine_store),
        project_task_store=overrides.get("project_task_store", task_store),
        project_store=overrides.get("project_store", project_store),
        chat_channels=overrides.get("chat_channels", chat_channels),
        chat_messages=overrides.get("chat_messages", chat_messages),
        bridge_sessions=overrides.get("bridge_sessions", bridge_sessions),
        config=overrides.get("config", None),
    )
    return state


def _routine(**overrides) -> dict:
    base = {
        "id": "rtn-abc123",
        "project_id": "prj-1",
        "title": "Nightly sweep",
        "body_template": "do the thing",
        "assignee_id": None,
        "trigger_kind": "cron",
        "cron_expr": "0 3 * * *",
        "enabled": 1,
        "next_fire": 1000.0,
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_fire_routine_always_creates_task():
    state = _make_state()
    routine = _routine()
    task = await fire_routine(state, routine)
    assert task["id"] == "tsk-new1"
    state.project_task_store.create_task.assert_awaited_once()
    kwargs = state.project_task_store.create_task.call_args.kwargs
    assert kwargs["project_id"] == "prj-1"
    assert kwargs["title"] == "Nightly sweep"
    assert kwargs["body"] == "do the thing"
    assert kwargs["created_by"] == "routine:rtn-abc123"


@pytest.mark.asyncio
async def test_fire_routine_records_fire():
    state = _make_state()
    routine = _routine()
    await fire_routine(state, routine)
    state.routine_store.record_fire.assert_awaited_once()
    call_args = state.routine_store.record_fire.call_args.args
    assert call_args[0] == "rtn-abc123"


@pytest.mark.asyncio
async def test_fire_routine_posts_a2a_system_message():
    state = _make_state()
    routine = _routine()
    await fire_routine(state, routine)
    state.chat_messages.send_message.assert_awaited_once()
    kwargs = state.chat_messages.send_message.call_args.kwargs
    assert kwargs["channel_id"] == "chan-a2a"
    assert kwargs["content_type"] == "system"
    assert "Nightly sweep" in kwargs["content"]


@pytest.mark.asyncio
async def test_fire_routine_creates_task_even_when_announce_fails():
    """Task creation must never be rolled back or skipped by a wake/announce
    failure — the wake edge is best-effort only."""
    state = _make_state()
    state.chat_messages.send_message = AsyncMock(side_effect=RuntimeError("boom"))
    routine = _routine()
    task = await fire_routine(state, routine)
    assert task["id"] == "tsk-new1"
    state.project_task_store.create_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_fire_routine_wakes_running_assignee_agent():
    config = SimpleNamespace(agents=[{"id": "agent-hex-1", "name": "worker-agent", "status": "running"}])
    state = _make_state(config=config)
    routine = _routine(assignee_id="agent-hex-1")
    task = await fire_routine(state, routine)
    state.bridge_sessions.enqueue_user_message.assert_awaited_once()
    args = state.bridge_sessions.enqueue_user_message.call_args.args
    assert args[0] == "worker-agent"
    assert args[1]["id"] == task["id"]


@pytest.mark.asyncio
async def test_fire_routine_does_not_wake_stopped_agent():
    config = SimpleNamespace(agents=[{"id": "agent-hex-1", "name": "worker-agent", "status": "stopped"}])
    state = _make_state(config=config)
    routine = _routine(assignee_id="agent-hex-1")
    await fire_routine(state, routine)
    state.bridge_sessions.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_fire_routine_does_not_wake_when_assignee_unresolved():
    config = SimpleNamespace(agents=[])
    state = _make_state(config=config)
    routine = _routine(assignee_id="unknown-agent")
    task = await fire_routine(state, routine)
    assert task["id"] == "tsk-new1"
    state.bridge_sessions.enqueue_user_message.assert_not_awaited()


def test_resolve_assignee_agent_matches_by_id_or_name():
    config = SimpleNamespace(agents=[{"id": "hex1", "name": "worker", "status": "running"}])
    assert _resolve_assignee_agent(config, "hex1")["name"] == "worker"
    assert _resolve_assignee_agent(config, "worker")["name"] == "worker"
    assert _resolve_assignee_agent(config, "nope") is None
    assert _resolve_assignee_agent(config, None) is None
    assert _resolve_assignee_agent(None, "hex1") is None


@pytest.mark.asyncio
async def test_fire_routine_resolves_native_member_hex_id_to_agent_slug():
    """A native project member stores the agent's config id (a 12-char hex, e.g.
    '91a640130122') as its member_id, and that is exactly what the UI assignee
    picker sends. Confirm a routine whose assignee_id is that hex id resolves to
    the agent's name (slug) and wakes it — proving the wake edge actually fires
    for real assignees, not just when id==name."""
    hex_id = "91a640130122"
    config = SimpleNamespace(
        agents=[{"id": hex_id, "name": "researcher", "status": "running"}]
    )
    state = _make_state(config=config)
    routine = _routine(assignee_id=hex_id)
    await fire_routine(state, routine)
    state.bridge_sessions.enqueue_user_message.assert_awaited_once()
    slug = state.bridge_sessions.enqueue_user_message.call_args.args[0]
    assert slug == "researcher"


@pytest.mark.asyncio
async def test_due_routine_fires_exactly_once_across_two_concurrent_ticks(tmp_path):
    """Two ticks that both selected the same due routine from list_due (same
    next_fire snapshot) must not both fire it: claim_due lets exactly one win,
    so the task is created exactly once."""
    store = RoutineStore(tmp_path / "routines.db")
    await store.init()
    try:
        r = await store.create_routine(
            project_id="prj-1", title="Once", created_by="u", cron_expr="* * * * *",
        )
        now = 2_000_000.0
        # Force it due at `now`.
        await store._db.execute(
            "UPDATE routines SET next_fire = ? WHERE id = ?", (now - 10, r["id"])
        )
        await store._db.commit()
        snapshot = await store.get_routine(r["id"])

        created = 0

        async def _create_task(**kwargs):
            nonlocal created
            created += 1
            return {"id": f"tsk-{created}", "project_id": "prj-1", "title": "Once"}

        state = _make_state(routine_store=store)
        state.project_task_store.create_task = AsyncMock(side_effect=_create_task)

        # Two back-to-back ticks, each holding the same pre-claim snapshot.
        for _ in range(2):
            claimed = await store.claim_due(snapshot["id"], snapshot["next_fire"], now)
            if claimed:
                await _create_task_and_wake(state, snapshot)

        assert created == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_routine_tick_loop_fires_due_routines_then_sleeps(monkeypatch):
    routine = _routine()
    state = _make_state()
    state.routine_store.list_due = AsyncMock(side_effect=[[routine], AssertionError("should not tick twice")])

    sleeps = []
    import asyncio as _asyncio

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        raise _asyncio.CancelledError()

    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)

    with pytest.raises(_asyncio.CancelledError):
        await routine_tick_loop(state)

    state.project_task_store.create_task.assert_awaited_once()
    assert sleeps == [60]


@pytest.mark.asyncio
async def test_routine_tick_loop_survives_a_bad_routine(monkeypatch):
    """One routine raising during fire must not stop the sweep from
    continuing to the next routine, nor crash the loop."""
    good = _routine(id="rtn-good")
    bad = _routine(id="rtn-bad")
    state = _make_state()
    state.routine_store.list_due = AsyncMock(return_value=[bad, good])

    calls = []

    async def _create_task(*args, **kwargs):
        calls.append(kwargs.get("created_by"))
        if kwargs.get("created_by") == "routine:rtn-bad":
            raise RuntimeError("boom")
        return {"id": "tsk-good", "project_id": "prj-1", "title": "ok"}

    state.project_task_store.create_task = AsyncMock(side_effect=_create_task)

    import asyncio as _asyncio

    async def _fake_sleep(seconds):
        raise _asyncio.CancelledError()

    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)

    with pytest.raises(_asyncio.CancelledError):
        await routine_tick_loop(state)

    assert calls == ["routine:rtn-bad", "routine:rtn-good"]
