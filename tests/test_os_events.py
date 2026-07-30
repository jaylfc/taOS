"""Tests for GET /api/os/events."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import State

from tinyagentos.events.bus import EventBus, SystemEvent
from tinyagentos.routes.os_events import router as os_events_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.event_bus = EventBus()

    @app.middleware("http")
    async def _auth_middleware(request, call_next):
        request.state.user_id = "test-user"
        request.state.is_admin = False
        return await call_next(request)

    app.include_router(os_events_router)
    return app


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_requires_auth():
    """Unauthenticated requests are rejected with 401."""
    app = FastAPI()
    app.state.event_bus = EventBus()
    app.include_router(os_events_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as no_auth:
        resp = await no_auth.get("/api/os/events")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_stream_requires_auth_explicit_setup():
    """Belt-and-braces: request.state.user_id=None gives 401."""
    from tinyagentos.routes.os_events import os_events
    from unittest.mock import MagicMock

    req = MagicMock()
    req.state.user_id = None
    resp = await os_events(req)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Streaming behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_delivers_subscribed_kinds():
    """An event whose kind matches the filter is delivered."""
    app = _make_app()
    bus: EventBus = app.state.event_bus

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "headers": [(b"accept", b"text/event-stream")],
        "scheme": "http",
        "path": "/api/os/events",
        "raw_path": b"/api/os/events?kinds=projects.task.changed",
        "query_string": b"kinds=projects.task.changed",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
        "state": State(),
    }
    scope["state"].user_id = "user-1"
    scope["state"].is_admin = False

    lines: list[str] = []
    done = asyncio.Event()

    async def receive():
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            for line in body.decode().split("\n"):
                s = line.rstrip("\r")
                if s.startswith("data:"):
                    lines.append(s)
                    done.set()

    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.2)

    await bus.broadcast(
        SystemEvent(
            kind="projects.task.changed",
            source="system",
            targets=["broadcast"],
            payload={"task_id": "tsk-123"},
        )
    )

    try:
        await asyncio.wait_for(done.wait(), timeout=3.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert lines, "no data: line received"
    evt = json.loads(lines[0][5:].strip())
    assert evt["kind"] == "projects.task.changed"
    assert "payload" not in evt
    assert evt["id"] is not None
    assert evt["ts"] is not None


@pytest.mark.asyncio
async def test_stream_filters_kinds():
    """An event whose kind does NOT match the filter is dropped."""
    app = _make_app()
    bus: EventBus = app.state.event_bus

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "headers": [(b"accept", b"text/event-stream")],
        "scheme": "http",
        "path": "/api/os/events",
        "raw_path": b"/api/os/events?kinds=projects.task.changed",
        "query_string": b"kinds=projects.task.changed",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
        "state": State(),
    }
    scope["state"].user_id = "user-1"
    scope["state"].is_admin = False

    lines: list[str] = []
    got_wrong_kind = asyncio.Event()

    async def receive():
        await got_wrong_kind.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            for line in body.decode().split("\n"):
                s = line.rstrip("\r")
                if s.startswith("data:"):
                    lines.append(s)
                    got_wrong_kind.set()

    task = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(0.2)

    await bus.broadcast(
        SystemEvent(
            kind="agents.status.changed",
            source="system",
            targets=["broadcast"],
            payload={"agent_id": "agent-1"},
        )
    )

    try:
        await asyncio.wait_for(got_wrong_kind.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass  # expected: wrong kind must not arrive

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert not lines, f"expected no events for wrong kind, got: {lines}"


@pytest.mark.asyncio
async def test_stream_two_subscribers_both_receive():
    """Two authenticated clients subscribed to the same kind both get the event."""
    app = _make_app()
    bus: EventBus = app.state.event_bus

    results: list[list[str]] = [[], []]
    dones = [asyncio.Event(), asyncio.Event()]
    tasks: list[asyncio.Task] = []

    for i in range(2):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "headers": [(b"accept", b"text/event-stream")],
            "scheme": "http",
            "path": "/api/os/events",
            "raw_path": b"/api/os/events",
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234 + i),
            "root_path": "",
            "state": State(),
        }
        scope["state"].user_id = f"user-{i}"
        scope["state"].is_admin = False

        async def receive(idx=i):
            await dones[idx].wait()
            return {"type": "http.disconnect"}

        async def send(message, idx=i):
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                for line in body.decode().split("\n"):
                    s = line.rstrip("\r")
                    if s.startswith("data:"):
                        results[idx].append(s)
                        dones[idx].set()

        tasks.append(asyncio.create_task(app(scope, receive, send)))

    await asyncio.sleep(0.2)

    await bus.broadcast(
        SystemEvent(
            kind="projects.task.changed",
            source="system",
            targets=["broadcast"],
            payload={"task_id": "tsk-123"},
        )
    )

    try:
        await asyncio.wait_for(dones[0].wait(), timeout=3.0)
        await asyncio.wait_for(dones[1].wait(), timeout=3.0)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    assert len(results[0]) == 1
    assert len(results[1]) == 1
    evt0 = json.loads(results[0][0][5:].strip())
    evt1 = json.loads(results[1][0][5:].strip())
    assert evt0["kind"] == "projects.task.changed"
    assert evt1["kind"] == "projects.task.changed"
    assert "payload" not in evt0
    assert "payload" not in evt1
    assert evt0["id"] is not None
    assert evt1["id"] is not None
