from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.agent_heartbeat import (
    REWAKE_COOLDOWN,
    _MAX_TICK_STAGGER_SECONDS,
    _WAKE_STAGGER_SECONDS,
    _heartbeat_tick,
    agent_heartbeat_loop,
)


def _make_state(**overrides) -> SimpleNamespace:
    task_store = MagicMock()
    task_store.held_task = AsyncMock(return_value=None)
    task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[])
    task_store.get_task_context = AsyncMock(
        return_value={
            "project": {"id": "prj-1", "name": "Demo", "description": ""},
            "ancestry": [],
            "blockers": [],
            "is_blocked": False,
        }
    )

    project_store = MagicMock()
    project_store.list_members = AsyncMock(return_value=[])
    project_store.get_project = AsyncMock(return_value={"id": "prj-1", "slug": "demo", "created_by": "u"})

    chat_channels = MagicMock()
    chat_channels.list_channels = AsyncMock(return_value=[])
    chat_channels.create_channel = AsyncMock(return_value={"id": "chan-a2a", "members": []})

    chat_messages = MagicMock()
    chat_messages.send_message = AsyncMock(return_value={"id": "msg-1"})

    bridge_sessions = MagicMock()
    bridge_sessions.enqueue_user_message = AsyncMock()

    config = overrides.get("config", SimpleNamespace(agents=[], server={"agent_heartbeat_enabled": True}))

    state = SimpleNamespace(
        project_task_store=overrides.get("project_task_store", task_store),
        project_store=overrides.get("project_store", project_store),
        chat_channels=overrides.get("chat_channels", chat_channels),
        chat_messages=overrides.get("chat_messages", chat_messages),
        bridge_sessions=overrides.get("bridge_sessions", bridge_sessions),
        config=config,
    )
    return state


def _task(**overrides) -> dict:
    base = {"id": "tsk-1", "project_id": "prj-1", "title": "Do the thing"}
    base.update(overrides)
    return base


def _agent(**overrides) -> dict:
    base = {"id": "agent-hex-1", "name": "worker-agent", "status": "running"}
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_disabled_setting_no_wake():
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": False})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[_task()])
    await _heartbeat_tick(state)
    state.bridge_sessions.enqueue_user_message.assert_not_awaited()
    state.chat_messages.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_setting_defaults_disabled():
    config = SimpleNamespace(agents=[_agent()], server={})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[_task()])
    await _heartbeat_tick(state)
    state.bridge_sessions.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_idle_running_agent_with_ready_task_wakes_once():
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    task = _task()
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[task])

    await _heartbeat_tick(state)

    state.bridge_sessions.enqueue_user_message.assert_awaited_once()
    args = state.bridge_sessions.enqueue_user_message.call_args.args
    assert args[0] == "worker-agent"
    # Message id is a fresh uuid (NOT the task id, which would collide across
    # repeated announcements); the task stays traceable via trace_id.
    assert args[1]["id"] != "tsk-1"
    assert args[1]["trace_id"] == "heartbeat-tsk-1"
    assert "tsk-1" in args[1]["text"]

    state.chat_messages.send_message.assert_awaited_once()
    kwargs = state.chat_messages.send_message.call_args.kwargs
    assert kwargs["channel_id"] == "chan-a2a"
    assert kwargs["content_type"] == "system"
    assert "tsk-1" in kwargs["content"]

    state.project_task_store.list_ready_tasks_for_assignee.assert_awaited_once_with("agent-hex-1")
    state.project_task_store.get_task_context.assert_awaited_once_with("tsk-1")


@pytest.mark.asyncio
async def test_agent_holding_a_task_is_not_woken():
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    state.project_task_store.held_task = AsyncMock(return_value="tsk-held")
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[_task()])

    await _heartbeat_tick(state)

    state.project_task_store.list_ready_tasks_for_assignee.assert_not_awaited()
    state.bridge_sessions.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_running_agent_is_not_woken():
    config = SimpleNamespace(agents=[_agent(status="stopped")], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[_task()])

    await _heartbeat_tick(state)

    state.bridge_sessions.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_ready_tasks_skips_agent():
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[])

    await _heartbeat_tick(state)

    state.bridge_sessions.enqueue_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_debounce_same_task_within_cooldown_wakes_only_once(monkeypatch):
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    task = _task()
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[task])

    # next(times, default): logging (LogRecord creation) can also call
    # time.time() under CI, so extra calls must not exhaust the iterator.
    times = iter([1_000.0, 1_000.0 + REWAKE_COOLDOWN / 2])
    monkeypatch.setattr(
        "tinyagentos.agent_heartbeat.time.time",
        lambda: next(times, 1_000.0 + REWAKE_COOLDOWN / 2),
    )

    await _heartbeat_tick(state)
    await _heartbeat_tick(state)

    state.bridge_sessions.enqueue_user_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_wake_is_not_debounced_and_retries_next_tick(monkeypatch):
    # A wake whose enqueue fails must NOT stamp the debounce; the next tick
    # retries instead of silencing the agent for the whole cooldown.
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    task = _task()
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[task])
    state.bridge_sessions.enqueue_user_message = AsyncMock(side_effect=RuntimeError("queue down"))

    # Two ticks well within cooldown; the warning log's LogRecord also calls
    # time.time(), so the patch must tolerate extra calls (next default).
    times = iter([1_000.0, 1_000.0 + 60.0])
    monkeypatch.setattr(
        "tinyagentos.agent_heartbeat.time.time", lambda: next(times, 1_000.0 + 60.0)
    )

    await _heartbeat_tick(state)
    await _heartbeat_tick(state)

    # Both ticks attempted the wake (no silencing after the failure).
    assert state.bridge_sessions.enqueue_user_message.await_count == 2


@pytest.mark.asyncio
async def test_debounce_expires_after_cooldown_rewakes(monkeypatch):
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    task = _task()
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[task])

    times = iter([1_000.0, 1_000.0 + REWAKE_COOLDOWN + 1])
    monkeypatch.setattr(
        "tinyagentos.agent_heartbeat.time.time",
        lambda: next(times, 1_000.0 + REWAKE_COOLDOWN + 1),
    )

    await _heartbeat_tick(state)
    await _heartbeat_tick(state)

    assert state.bridge_sessions.enqueue_user_message.await_count == 2


@pytest.mark.asyncio
async def test_one_bad_agent_does_not_break_the_tick():
    good = _agent(id="agent-good", name="good-agent")
    bad = _agent(id="agent-bad", name="bad-agent")
    config = SimpleNamespace(agents=[bad, good], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)

    async def _ready(assignee_id):
        if assignee_id == "agent-bad":
            raise RuntimeError("boom")
        return [_task(id="tsk-good")]

    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(side_effect=_ready)

    await _heartbeat_tick(state)

    state.bridge_sessions.enqueue_user_message.assert_awaited_once()
    args = state.bridge_sessions.enqueue_user_message.call_args.args
    assert args[0] == "good-agent"


@pytest.mark.asyncio
async def test_wake_stagger_between_successive_wakes(monkeypatch):
    # Two idle running agents both with a ready task: the tick must stagger the
    # second wake by _WAKE_STAGGER_SECONDS so their downstream LLM turns don't
    # fire at one instant. The patched sleep records instead of sleeping, so the
    # test stays fast.
    a = _agent(id="agent-a", name="agent-a")
    b = _agent(id="agent-b", name="agent-b")
    config = SimpleNamespace(agents=[a, b], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)

    async def _ready(assignee_id):
        return [_task(id=f"tsk-{assignee_id}")]

    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(side_effect=_ready)

    sleeps = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("tinyagentos.agent_heartbeat.asyncio.sleep", _fake_sleep)

    await _heartbeat_tick(state)

    # Both agents woken; exactly one stagger sleep sits between the two wakes
    # (no leading/trailing sleep).
    assert state.bridge_sessions.enqueue_user_message.await_count == 2
    assert sleeps == [_WAKE_STAGGER_SECONDS]


@pytest.mark.asyncio
async def test_single_wake_has_no_stagger(monkeypatch):
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[_task()])

    sleeps = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("tinyagentos.agent_heartbeat.asyncio.sleep", _fake_sleep)

    await _heartbeat_tick(state)

    state.bridge_sessions.enqueue_user_message.assert_awaited_once()
    assert sleeps == []


@pytest.mark.asyncio
async def test_wake_stagger_is_capped_on_large_fleet(monkeypatch):
    # The cumulative per-tick stagger is bounded so a large fleet cannot push a
    # tick past the next sweep: once the budget is spent, remaining wakes fire
    # with no added delay.
    cap_wakes = int(_MAX_TICK_STAGGER_SECONDS / _WAKE_STAGGER_SECONDS)
    n = cap_wakes + 20  # comfortably past the budget
    agents = [_agent(id=f"agent-{i}", name=f"agent-{i}") for i in range(n)]
    config = SimpleNamespace(agents=agents, server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(
        side_effect=lambda assignee_id: [_task(id=f"tsk-{assignee_id}")]
    )

    sleeps = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("tinyagentos.agent_heartbeat.asyncio.sleep", _fake_sleep)

    await _heartbeat_tick(state)

    # All agents were woken, but the number of stagger sleeps is capped by the
    # budget, not by the fleet size, and the total never exceeds the cap.
    assert state.bridge_sessions.enqueue_user_message.await_count == n
    assert len(sleeps) == cap_wakes
    assert sum(sleeps) <= _MAX_TICK_STAGGER_SECONDS


@pytest.mark.asyncio
async def test_agent_heartbeat_loop_ticks_then_sleeps(monkeypatch):
    config = SimpleNamespace(agents=[_agent()], server={"agent_heartbeat_enabled": True})
    state = _make_state(config=config)
    state.project_task_store.list_ready_tasks_for_assignee = AsyncMock(return_value=[_task()])

    sleeps = []
    import asyncio as _asyncio

    async def _fake_sleep(seconds):
        sleeps.append(seconds)
        raise _asyncio.CancelledError()

    monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)

    with pytest.raises(_asyncio.CancelledError):
        await agent_heartbeat_loop(state)

    state.bridge_sessions.enqueue_user_message.assert_awaited_once()
    assert sleeps == [60]
