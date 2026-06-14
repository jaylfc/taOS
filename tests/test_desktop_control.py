import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tinyagentos.desktop_control.broker import DesktopCommand, DesktopCommandBroker
from tinyagentos.routes.desktop_control import router


# --------------------------------------------------------------------------- #
# Broker unit tests                                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_emit_fans_out_to_subscribers():
    broker = DesktopCommandBroker()
    a = await broker.subscribe("user-1")
    b = await broker.subscribe("user-1")
    reached = await broker.emit("user-1", DesktopCommand(kind="open-app", payload={"app": "projects"}))
    assert reached == 2
    cmd_a = await asyncio.wait_for(a.get(), timeout=0.5)
    cmd_b = await asyncio.wait_for(b.get(), timeout=0.5)
    assert cmd_a.kind == "open-app" and cmd_a.payload["app"] == "projects"
    assert cmd_b.payload["app"] == "projects"


@pytest.mark.asyncio
async def test_emit_isolated_per_user():
    broker = DesktopCommandBroker()
    a = await broker.subscribe("user-1")
    b = await broker.subscribe("user-2")
    await broker.emit("user-1", DesktopCommand(kind="open-app", payload={"app": "files"}))
    got = await asyncio.wait_for(a.get(), timeout=0.5)
    assert got.payload["app"] == "files"
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(b.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_no_replay_for_late_subscriber():
    """A command must NOT be re-delivered to a desktop that connects later, or
    closed apps would re-open on reconnect."""
    broker = DesktopCommandBroker()
    await broker.emit("user-1", DesktopCommand(kind="open-app", payload={"app": "chat"}))
    late = await broker.subscribe("user-1")
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(late.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_overflow_drops_oldest_keeps_newest():
    """A stalled desktop's bounded queue drops the OLDEST command, never grows
    unbounded, and still surfaces the most recent commands."""
    broker = DesktopCommandBroker()
    q = await broker.subscribe("user-1")
    n = DesktopCommandBroker._MAX_QUEUE + 5
    for i in range(n):
        await broker.emit("user-1", DesktopCommand(kind="open-app", payload={"app": f"app-{i}"}))
    assert q.qsize() == DesktopCommandBroker._MAX_QUEUE
    # The newest command must still be in the queue (oldest were dropped).
    apps = []
    while not q.empty():
        apps.append(q.get_nowait().payload["app"])
    assert apps[-1] == f"app-{n - 1}"
    assert f"app-0" not in apps


@pytest.mark.asyncio
async def test_emit_with_no_subscribers_returns_zero():
    broker = DesktopCommandBroker()
    assert await broker.emit("nobody", DesktopCommand(kind="window", payload={"action": "arrange"})) == 0


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    broker = DesktopCommandBroker()
    q = await broker.subscribe("user-1")
    await broker.unsubscribe("user-1", q)
    assert await broker.emit("user-1", DesktopCommand(kind="open-app", payload={"app": "x"})) == 0


# --------------------------------------------------------------------------- #
# Route tests                                                                  #
# --------------------------------------------------------------------------- #
def _make_app() -> FastAPI:
    app = FastAPI()
    app.state.desktop_command_broker = DesktopCommandBroker()
    app.include_router(router)
    return app


@pytest.mark.asyncio
async def test_post_command_validates_kind():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/desktop/command", json={"kind": "bogus", "payload": {}})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_command_no_desktop_delivers_zero():
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/api/desktop/command", json={"kind": "open-app", "payload": {"app": "projects"}})
        assert r.status_code == 200
        assert r.json()["delivered"] == 0


@pytest.mark.asyncio
async def test_stream_receives_emitted_command():
    """Drive the SSE endpoint over raw ASGI, subscribe, then emit and collect."""
    app = _make_app()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "headers": [(b"accept", b"text/event-stream")],
        "scheme": "http",
        "path": "/api/desktop/stream",
        "raw_path": b"/api/desktop/stream",
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
    }
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
    # Give the stream a moment to subscribe (no replay, so emit must come after).
    await asyncio.sleep(0.2)
    reached = await app.state.desktop_command_broker.emit(
        "system", DesktopCommand(kind="open-app", payload={"app": "projects"})
    )
    assert reached == 1, "the SSE subscriber should be the sole 'system' desktop"
    try:
        await asyncio.wait_for(done.wait(), timeout=3.0)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    import json
    assert lines, "no data: line received"
    evt = json.loads(lines[0][5:].strip())
    assert evt["kind"] == "open-app"
    assert evt["payload"]["app"] == "projects"
