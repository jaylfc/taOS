"""Tests for trace HTTP routes."""
from __future__ import annotations

import pytest

from tinyagentos.trace_store import TraceStoreRegistry, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inject_registry(app, tmp_path):
    """Replace the app-level trace_registry with a fresh one backed by tmp_path."""
    registry = TraceStoreRegistry(tmp_path)
    app.state.trace_registry = registry
    return registry


# ---------------------------------------------------------------------------
# POST /api/trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_trace_success(client, tmp_path):
    _inject_registry(client._transport.app, tmp_path)
    resp = await client.post("/api/trace", json={
        "agent_name": "my-agent",
        "kind": "message_in",
        "payload": {"from": "user", "text": "hello"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_name"] == "my-agent"
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["id"]


@pytest.mark.asyncio
async def test_post_trace_unknown_kind(client, tmp_path):
    _inject_registry(client._transport.app, tmp_path)
    resp = await client.post("/api/trace", json={
        "agent_name": "my-agent",
        "kind": "not_a_kind",
        "payload": {},
    })
    assert resp.status_code == 400
    assert "unknown kind" in resp.json()["error"]


@pytest.mark.asyncio
async def test_post_trace_all_fields(client, tmp_path):
    _inject_registry(client._transport.app, tmp_path)
    resp = await client.post("/api/trace", json={
        "agent_name": "full-agent",
        "kind": "llm_call",
        "trace_id": "tr-xyz",
        "channel_id": "ch-1",
        "model": "gpt-4",
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": 0.001,
        "duration_ms": 320,
        "payload": {
            "status": "success",
            "messages": [{"role": "user", "content": "hi"}],
            "response": "hello",
            "metadata": {},
        },
    })
    assert resp.status_code == 200
    assert resp.json()["agent_name"] == "full-agent"


@pytest.mark.asyncio
async def test_post_trace_no_registry(client):
    # Remove the registry temporarily
    app = client._transport.app
    original = app.state.trace_registry
    del app.state.trace_registry
    try:
        resp = await client.post("/api/trace", json={
            "agent_name": "x",
            "kind": "lifecycle",
            "payload": {"event": "start"},
        })
        assert resp.status_code == 503
    finally:
        app.state.trace_registry = original


# ---------------------------------------------------------------------------
# GET /api/agents/{name}/trace
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_trace_empty(client, tmp_path):
    _inject_registry(client._transport.app, tmp_path)
    resp = await client.get("/api/agents/no-events-agent/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_name"] == "no-events-agent"
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["events"] == []


@pytest.mark.asyncio
async def test_get_trace_returns_events(client, tmp_path):
    registry = _inject_registry(client._transport.app, tmp_path)

    # Post two events
    for i in range(3):
        await client.post("/api/trace", json={
            "agent_name": "list-agent",
            "kind": "lifecycle",
            "payload": {"event": f"e{i}"},
        })

    resp = await client.get("/api/agents/list-agent/trace")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 3


@pytest.mark.asyncio
async def test_get_trace_filter_by_kind(client, tmp_path):
    _inject_registry(client._transport.app, tmp_path)

    await client.post("/api/trace", json={"agent_name": "filter-agent", "kind": "message_in", "payload": {"from": "u", "text": "a"}})
    await client.post("/api/trace", json={"agent_name": "filter-agent", "kind": "message_out", "payload": {"content": "b"}})

    resp = await client.get("/api/agents/filter-agent/trace", params={"kind": "message_in"})
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert all(e["kind"] == "message_in" for e in events)
    assert len(events) == 1


@pytest.mark.asyncio
async def test_get_trace_no_registry(client):
    app = client._transport.app
    original = app.state.trace_registry
    del app.state.trace_registry
    try:
        resp = await client.get("/api/agents/x/trace")
        assert resp.status_code == 503
    finally:
        app.state.trace_registry = original


# ---------------------------------------------------------------------------
# POST /api/lifecycle/notify
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifecycle_notify_success(client):
    from unittest.mock import MagicMock
    app = client._transport.app
    mock_mgr = MagicMock()
    mock_mgr.notify_task_complete = MagicMock()
    original = getattr(app.state, "lifecycle_manager", None)
    app.state.lifecycle_manager = mock_mgr
    try:
        resp = await client.post("/api/lifecycle/notify", json={"backend_name": "my-backend"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "noted"
        assert body["backend_name"] == "my-backend"
        mock_mgr.notify_task_complete.assert_called_once_with("my-backend")
    finally:
        if original is not None:
            app.state.lifecycle_manager = original
        else:
            del app.state.lifecycle_manager


@pytest.mark.asyncio
async def test_lifecycle_notify_no_manager(client):
    """503 when lifecycle_manager is not on app.state."""
    app = client._transport.app
    # Make lifecycle_manager absent by setting a sentinel then temporarily
    # replacing it with None so getattr returns None.
    original = getattr(app.state, "lifecycle_manager", None)
    app.state.lifecycle_manager = None
    try:
        resp = await client.post("/api/lifecycle/notify", json={"backend_name": "x"})
        assert resp.status_code == 503
    finally:
        if original is not None:
            app.state.lifecycle_manager = original
        else:
            app.state.lifecycle_manager = None
