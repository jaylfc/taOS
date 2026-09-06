"""Endpoint tests for tinyagentos/routes/events.py."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
import pytest_asyncio

from tinyagentos.events.bus import SystemEvent
from tinyagentos.events.store import SystemEventStore


@pytest_asyncio.fixture(autouse=True)
async def _init_system_events(client, tmp_path):
    """Ensure app.state.system_events is initialised for every test."""
    store = SystemEventStore(tmp_path / "system-events.db")
    await store.init()
    client._transport.app.state.system_events = store
    yield
    await store.close()


@pytest.mark.asyncio
async def test_list_events_returns_200(client):
    resp = await client.get("/api/events")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_events_response_shape(client):
    data = (await client.get("/api/events")).json()
    assert "events" in data
    assert "count" in data
    assert isinstance(data["events"], list)
    assert isinstance(data["count"], int)
    assert data["count"] == len(data["events"])


@pytest.mark.asyncio
async def test_list_events_empty_store(client):
    data = (await client.get("/api/events")).json()
    assert data["events"] == []
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_list_events_returns_events_newest_first(client):
    store = client._transport.app.state.system_events
    now = time.time()
    for i in range(3):
        await store.add(SystemEvent(
            kind="test",
            source="pytest",
            targets=["t1"],
            level="info",
            payload={"idx": i},
            ts=now + i,
            trace_id=f"trace-{i}",
        ))
    data = (await client.get("/api/events")).json()
    assert data["count"] == 3
    timestamps = [e["ts"] for e in data["events"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_list_events_limit_param(client):
    store = client._transport.app.state.system_events
    now = time.time()
    for i in range(5):
        await store.add(SystemEvent(
            kind="test",
            source="pytest",
            targets=["t1"],
            level="info",
            payload={"idx": i},
            ts=now + i,
            trace_id=f"trace-{i}",
        ))
    data = (await client.get("/api/events?limit=2")).json()
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_list_events_limit_clamped_high(client):
    """Limit above 1000 is clamped to 1000 by the store."""
    store = client._transport.app.state.system_events
    now = time.time()
    for i in range(3):
        await store.add(SystemEvent(
            kind="test",
            source="pytest",
            targets=["t1"],
            level="info",
            payload={"idx": i},
            ts=now + i,
            trace_id=f"trace-{i}",
        ))
    data = (await client.get("/api/events?limit=9999")).json()
    assert data["count"] == 3


@pytest.mark.asyncio
async def test_list_events_limit_clamped_low(client):
    """Limit below 1 is clamped to 1 by the store."""
    store = client._transport.app.state.system_events
    now = time.time()
    for i in range(3):
        await store.add(SystemEvent(
            kind="test",
            source="pytest",
            targets=["t1"],
            level="info",
            payload={"idx": i},
            ts=now + i,
            trace_id=f"trace-{i}",
        ))
    data = (await client.get("/api/events?limit=0")).json()
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_list_events_kind_filter(client):
    store = client._transport.app.state.system_events
    now = time.time()
    await store.add(SystemEvent(
        kind="agent.lifecycle",
        source="orchestrator",
        targets=["agent-1"],
        level="info",
        payload={"action": "start"},
        ts=now,
        trace_id="t-1",
    ))
    await store.add(SystemEvent(
        kind="user.message",
        source="gateway",
        targets=["agent-1"],
        level="info",
        payload={"text": "hello"},
        ts=now + 1,
        trace_id="t-2",
    ))
    data = (await client.get("/api/events?kind=agent.lifecycle")).json()
    assert data["count"] == 1
    assert data["events"][0]["kind"] == "agent.lifecycle"


@pytest.mark.asyncio
async def test_list_events_kind_filter_no_match(client):
    store = client._transport.app.state.system_events
    now = time.time()
    await store.add(SystemEvent(
        kind="agent.lifecycle",
        source="orchestrator",
        targets=["agent-1"],
        level="info",
        payload={"action": "start"},
        ts=now,
        trace_id="t-1",
    ))
    data = (await client.get("/api/events?kind=nonexistent")).json()
    assert data["count"] == 0
    assert data["events"] == []


@pytest.mark.asyncio
async def test_list_events_event_fields(client):
    """Each event dict should expose the expected keys."""
    store = client._transport.app.state.system_events
    now = time.time()
    await store.add(SystemEvent(
        kind="test.kind",
        source="pytest",
        targets=["target-a"],
        level="warn",
        payload={"key": "value"},
        ts=now,
        trace_id="abc-123",
    ))
    data = (await client.get("/api/events")).json()
    event = data["events"][0]
    for key in ("id", "kind", "source", "targets", "level", "payload", "ts", "trace_id"):
        assert key in event, f"missing event key: {key}"
    assert event["kind"] == "test.kind"
    assert event["source"] == "pytest"
    assert event["targets"] == ["target-a"]
    assert event["level"] == "warn"
    assert event["payload"] == {"key": "value"}
    assert event["trace_id"] == "abc-123"


@pytest.mark.asyncio
async def test_list_events_default_limit_is_100(client):
    """Without a limit param the default is 100."""
    store = client._transport.app.state.system_events
    now = time.time()
    for i in range(150):
        await store.add(SystemEvent(
            kind="flood",
            source="pytest",
            targets=[],
            level="info",
            payload={"i": i},
            ts=now + i,
            trace_id=f"t-{i}",
        ))
    data = (await client.get("/api/events")).json()
    assert data["count"] == 100
