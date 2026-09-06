"""Tests for GET /api/events/stream and the NotificationStore event emitter."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tinyagentos.events.bus import EventBus, SystemEvent
from tinyagentos.notifications import NotificationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(**kwargs) -> SystemEvent:
    defaults = dict(
        kind="notification.added",
        source="system",
        targets=["broadcast"],
        payload={"id": 1, "title": "T", "message": "M", "level": "info"},
    )
    defaults.update(kwargs)
    return SystemEvent(**defaults)


@pytest_asyncio.fixture
async def notif_store(tmp_path):
    store = NotificationStore(tmp_path / "notifications.db")
    await store.init()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# EventBus.broadcast()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_broadcast_delivers_to_broadcast_channel():
    bus = EventBus()
    q = await bus.subscribe("broadcast")
    ev = _make_event()
    await bus.broadcast(ev)
    received = q.get_nowait()
    assert received is ev


@pytest.mark.asyncio
async def test_broadcast_does_not_deliver_to_other_channels():
    bus = EventBus()
    other_q = await bus.subscribe("user:alice")
    bcast_q = await bus.subscribe("broadcast")
    ev = _make_event()
    await bus.broadcast(ev)
    # broadcast channel gets it
    assert not bcast_q.empty()
    # user channel does not
    assert other_q.empty()


@pytest.mark.asyncio
async def test_broadcast_multiple_subscribers_all_receive():
    bus = EventBus()
    q1 = await bus.subscribe("broadcast")
    q2 = await bus.subscribe("broadcast")
    ev = _make_event()
    await bus.broadcast(ev)
    assert q1.get_nowait() is ev
    assert q2.get_nowait() is ev


# ---------------------------------------------------------------------------
# NotificationStore.add() emitter wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_calls_event_emitter_with_correct_payload(notif_store):
    received: list[dict] = []

    async def emitter(row: dict) -> None:
        received.append(row)

    notif_store.set_event_emitter(emitter)
    await notif_store.add("Hello", "World", level="warning", source="tests")

    assert len(received) == 1
    row = received[0]
    assert row["title"] == "Hello"
    assert row["message"] == "World"
    assert row["level"] == "warning"
    assert row["source"] == "tests"
    assert row["read"] is False
    assert isinstance(row["id"], int)
    assert isinstance(row["timestamp"], int)


@pytest.mark.asyncio
async def test_add_emitter_receives_data_field(notif_store):
    received: list[dict] = []

    async def emitter(row: dict) -> None:
        received.append(row)

    notif_store.set_event_emitter(emitter)
    await notif_store.add("T", "M", data={"request_id": "abc"})

    assert received[0]["data"] == {"request_id": "abc"}


@pytest.mark.asyncio
async def test_add_raising_emitter_does_not_break_add(notif_store):
    async def bad_emitter(row: dict) -> None:
        raise RuntimeError("emitter exploded")

    notif_store.set_event_emitter(bad_emitter)
    # Should not raise
    await notif_store.add("Title", "Body")
    # Notification was persisted despite the emitter failing
    rows = await notif_store.list()
    assert len(rows) == 1
    assert rows[0]["title"] == "Title"


@pytest.mark.asyncio
async def test_add_without_emitter_still_works(notif_store):
    """No emitter set: add() works normally."""
    await notif_store.add("T", "M")
    rows = await notif_store.list()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# GET /api/events/stream — auth gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_requires_auth(client):
    """The SSE stream returns 401 for requests without a session."""
    # Create a client without auth credentials
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=client._transport.app)
    async with AsyncClient(transport=transport, base_url="http://test") as no_auth:
        resp = await no_auth.get("/api/events/stream")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_requires_auth_explicit_setup(app):
    """Belt-and-braces: check that request.state.user_id=None gives 401."""
    from tinyagentos.routes.event_stream import events_stream
    from unittest.mock import MagicMock, AsyncMock

    req = MagicMock()
    req.state.user_id = None
    resp = await events_stream(req)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# EventBus broadcast integration: notification.added flows to broadcast channel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notification_add_reaches_event_bus_broadcast(notif_store):
    """Full seam: add() → emitter → EventBus.broadcast() → subscriber."""
    bus = EventBus()
    bcast_q = await bus.subscribe("broadcast")

    async def emitter(row: dict) -> None:
        ev = SystemEvent(
            kind="notification.added",
            source="system",
            targets=["broadcast"],
            payload=row,
        )
        await bus.broadcast(ev)

    notif_store.set_event_emitter(emitter)
    await notif_store.add("Push Test", "via emitter")

    received = bcast_q.get_nowait()
    assert received.kind == "notification.added"
    assert received.payload["title"] == "Push Test"


@pytest.mark.asyncio
async def test_notification_add_with_user_id_routes_to_user_channel(notif_store):
    """A notification with user_id set must reach the per-user channel, not broadcast."""
    bus = EventBus()
    user_q = await bus.subscribe("user:alice")
    bcast_q = await bus.subscribe("broadcast")

    async def emitter(row: dict) -> None:
        ev = SystemEvent(
            kind="notification.added",
            source="system",
            targets=["broadcast"],
            payload=row,
        )
        uid = row.get("user_id")
        if uid:
            await bus.publish_to(f"user:{uid}", ev)
        else:
            await bus.broadcast(ev)

    notif_store.set_event_emitter(emitter)
    await notif_store.add("Alice only", "secret msg", user_id="alice")

    assert user_q.get_nowait() is not None
    assert bcast_q.empty(), "user-scoped notification must not leak to broadcast"


@pytest.mark.asyncio
async def test_notification_add_without_user_id_still_broadcasts(notif_store):
    """A notification without user_id (system-wide) must still reach broadcast."""
    bus = EventBus()
    bcast_q = await bus.subscribe("broadcast")

    async def emitter(row: dict) -> None:
        ev = SystemEvent(
            kind="notification.added",
            source="system",
            targets=["broadcast"],
            payload=row,
        )
        uid = row.get("user_id")
        if uid:
            await bus.publish_to(f"user:{uid}", ev)
        else:
            await bus.broadcast(ev)

    notif_store.set_event_emitter(emitter)
    await notif_store.add("System alert", "everyone sees this")

    assert bcast_q.get_nowait() is not None
