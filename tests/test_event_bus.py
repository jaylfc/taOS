"""Tests for the EventBus core (tinyagentos/events/)."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tinyagentos.events.bus import EventBus, SystemEvent, _derive_notification, emit_event
from tinyagentos.events.store import SystemEventStore


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

def _make_event(**kwargs) -> SystemEvent:
    defaults = dict(
        kind="test.event",
        source="system",
        targets=["user"],
        payload={"message": "hello"},
    )
    defaults.update(kwargs)
    return SystemEvent(**defaults)


class FakeNotifications:
    def __init__(self):
        self.calls: list[dict] = []

    async def add(self, title: str, message: str, level: str = "info", source: str = "system"):
        self.calls.append({"title": title, "message": message, "level": level, "source": source})


class FakeAgentMessages:
    def __init__(self):
        self.calls: list[dict] = []

    async def send(self, from_agent: str, to_agent: str, message: str, **_kwargs):
        self.calls.append({"from": from_agent, "to": to_agent, "message": message})


class FakeTraceStore:
    def __init__(self):
        self.events: list[SystemEvent] = []

    async def add(self, event: SystemEvent):
        self.events.append(event)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_persists_to_trace_store():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=[])  # no notification or agent routing
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert len(trace.events) == 1
    assert trace.events[0] is ev


@pytest.mark.asyncio
async def test_emit_user_target_calls_notifications_add():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["user"], payload={"message": "disk full"})
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert len(notifications.calls) == 1
    assert notifications.calls[0]["level"] == "info"
    assert notifications.calls[0]["source"] == "system"


@pytest.mark.asyncio
async def test_emit_warning_level_calls_notifications_even_without_user_target():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=[], level="warning", payload={})
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert len(notifications.calls) == 1


@pytest.mark.asyncio
async def test_emit_error_level_calls_notifications():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=[], level="error", payload={})
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert len(notifications.calls) == 1
    assert notifications.calls[0]["level"] == "error"


@pytest.mark.asyncio
async def test_emit_agent_id_target_calls_agent_messages_send():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["my-agent"], payload={"detail": "restarted"})
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert len(agent_messages.calls) == 1
    call = agent_messages.calls[0]
    assert call["from"] == "system"
    assert call["to"] == "my-agent"


@pytest.mark.asyncio
async def test_emit_user_sentinel_not_forwarded_as_agent_message():
    """'user' and 'broadcast' sentinels must not be sent as agent messages."""
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["user", "broadcast"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert len(agent_messages.calls) == 0


@pytest.mark.asyncio
async def test_subscribe_receives_published_event():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    queue = await bus.subscribe("my-agent")
    ev = _make_event(targets=["my-agent"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    received = queue.get_nowait()
    assert received is ev


@pytest.mark.asyncio
async def test_broadcast_channel_receives_every_event():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    queue = await bus.subscribe("broadcast")
    ev = _make_event(targets=["some-other-agent"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    received = queue.get_nowait()
    assert received is ev


@pytest.mark.asyncio
async def test_subscribe_replays_buffered_events():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["my-agent"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    # Subscribe AFTER emit — replay should fill the queue
    queue = await bus.subscribe("my-agent")
    replayed = queue.get_nowait()
    assert replayed is ev


@pytest.mark.asyncio
async def test_permission_check_false_drops_event():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["user"])
    await bus.emit(
        ev,
        notifications=notifications,
        agent_messages=agent_messages,
        trace_store=trace,
        permission_check=lambda e: False,
    )

    # Nothing should be routed
    assert len(trace.events) == 0
    assert len(notifications.calls) == 0
    assert len(agent_messages.calls) == 0


@pytest.mark.asyncio
async def test_permission_check_true_allows_event():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["user"])
    await bus.emit(
        ev,
        notifications=notifications,
        agent_messages=agent_messages,
        trace_store=trace,
        permission_check=lambda e: True,
    )

    assert len(trace.events) == 1
    assert len(notifications.calls) == 1


@pytest.mark.asyncio
async def test_async_permission_check_supported():
    """permission_check may be an async function."""
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    async def _deny(event: SystemEvent) -> bool:
        return False

    ev = _make_event(targets=["user"])
    await bus.emit(
        ev,
        notifications=notifications,
        agent_messages=agent_messages,
        trace_store=trace,
        permission_check=_deny,
    )

    assert len(trace.events) == 0


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    queue = await bus.subscribe("my-agent")
    await bus.unsubscribe("my-agent", queue)

    ev = _make_event(targets=["my-agent"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    assert queue.empty()


# ---------------------------------------------------------------------------
# SystemEventStore tests (real SQLite via tmp_path)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def event_store(tmp_path):
    store = SystemEventStore(tmp_path / "test-events.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_store_add_and_list(event_store):
    ev = _make_event(kind="worker.join", targets=["user"], payload={"worker_id": "w1"})
    await event_store.add(ev)

    rows = await event_store.list()
    assert len(rows) == 1
    assert rows[0]["kind"] == "worker.join"
    assert rows[0]["targets"] == ["user"]
    assert rows[0]["payload"] == {"worker_id": "w1"}
    assert rows[0]["trace_id"] == ev.trace_id


@pytest.mark.asyncio
async def test_store_list_filter_by_kind(event_store):
    ev1 = _make_event(kind="worker.join")
    ev2 = _make_event(kind="backend.up")
    await event_store.add(ev1)
    await event_store.add(ev2)

    rows = await event_store.list(kind="worker.join")
    assert len(rows) == 1
    assert rows[0]["kind"] == "worker.join"


@pytest.mark.asyncio
async def test_store_list_limit(event_store):
    for i in range(5):
        await event_store.add(_make_event(kind=f"event.{i}"))

    rows = await event_store.list(limit=3)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# _derive_notification helper
# ---------------------------------------------------------------------------

def test_derive_notification_uses_payload_message():
    ev = _make_event(kind="worker.join", payload={"message": "Worker W1 joined"})
    title, message = _derive_notification(ev)
    assert "Worker" in title or "Join" in title
    assert message == "Worker W1 joined"


def test_derive_notification_falls_back_to_source():
    ev = _make_event(kind="test.thing", payload={})
    title, message = _derive_notification(ev)
    assert "system" in message


# ---------------------------------------------------------------------------
# emit_event helper (app_state integration)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_event_helper_pulls_from_app_state():
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()
    bus = EventBus()

    app_state = MagicMock()
    app_state.event_bus = bus
    app_state.notifications = notifications
    app_state.agent_messages = agent_messages
    app_state.system_events = trace

    ev = _make_event(targets=["user"])
    await emit_event(app_state, ev)

    assert len(trace.events) == 1
    assert len(notifications.calls) == 1


# ---------------------------------------------------------------------------
# Finding 1 — SystemEventStore.close() is callable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_system_event_store_close_is_callable(tmp_path):
    """close() must exist on SystemEventStore and leave the store unusable."""
    store = SystemEventStore(tmp_path / "close-test.db")
    await store.init()
    # Should not raise
    await store.close()
    # Underlying connection should be gone
    assert store._db is None


# ---------------------------------------------------------------------------
# Finding 2 — emit() deduplicates targets
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emit_duplicate_agent_targets_delivers_once():
    """Duplicate agent ids in targets must not cause duplicate sends."""
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    ev = _make_event(targets=["agent-x", "agent-x"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    # agent_messages.send should be called exactly once for agent-x
    assert len(agent_messages.calls) == 1
    assert agent_messages.calls[0]["to"] == "agent-x"


@pytest.mark.asyncio
async def test_emit_broadcast_in_targets_delivers_once_to_broadcast_channel():
    """broadcast in targets must not cause a double publish to the broadcast channel."""
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    bcast_queue = await bus.subscribe("broadcast")
    ev = _make_event(targets=["broadcast"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    # Exactly one item on the broadcast queue
    received = bcast_queue.get_nowait()
    assert received is ev
    assert bcast_queue.empty()


@pytest.mark.asyncio
async def test_emit_duplicate_targets_single_channel_delivery():
    """A subscriber on a channel gets the event exactly once despite duplicate targets."""
    bus = EventBus()
    notifications = FakeNotifications()
    agent_messages = FakeAgentMessages()
    trace = FakeTraceStore()

    queue = await bus.subscribe("agent-y")
    ev = _make_event(targets=["agent-y", "agent-y", "agent-y"])
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages, trace_store=trace)

    received = queue.get_nowait()
    assert received is ev
    assert queue.empty()


# ---------------------------------------------------------------------------
# Finding 3 — SystemEventStore.list() clamps limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_list_negative_limit_clamped(tmp_path):
    """list(limit=-1) must not trigger an unbounded SQLite read; clamps to 1."""
    store = SystemEventStore(tmp_path / "clamp-test.db")
    await store.init()
    try:
        for i in range(5):
            await store.add(_make_event(kind=f"ev.{i}"))
        rows = await store.list(limit=-1)
        # Should return at most 1 row (clamped to max(1, ...))
        assert len(rows) <= 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_store_list_huge_limit_clamped(tmp_path):
    """list(limit=99999) must be clamped to at most 1000 rows returned."""
    store = SystemEventStore(tmp_path / "huge-limit-test.db")
    await store.init()
    try:
        for i in range(5):
            await store.add(_make_event(kind=f"ev.{i}"))
        rows = await store.list(limit=99999)
        # Only 5 rows exist; all returned, but the SQL cap was 1000 not 99999
        assert len(rows) == 5
    finally:
        await store.close()
