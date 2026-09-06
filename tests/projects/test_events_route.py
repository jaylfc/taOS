"""Tests for GET /api/projects/{id}/events SSE route."""
import asyncio
import json
import pytest
from tinyagentos.projects.events import ProjectEvent


async def _collect_sse_lines(app, path, cookies, n_lines, timeout=5.0):
    """
    Drive an ASGI SSE endpoint, collect the first n_lines non-empty lines,
    then cancel the request task.

    Returns the collected lines.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "headers": [
            (b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()),
            (b"accept", b"text/event-stream"),
        ],
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
    }

    lines: list[str] = []
    response_started = asyncio.Event()
    done = asyncio.Event()

    async def receive():
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            response_started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                chunk = body.decode()
                for line in chunk.split("\n"):
                    stripped = line.rstrip("\r")
                    if stripped:
                        lines.append(stripped)
                        if len(lines) >= n_lines:
                            done.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(asyncio.shield(done.wait()), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    return lines


@pytest.mark.asyncio
async def test_sse_emits_task_event(app, client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "p"})).json()["id"]

    # Publish before subscribing so the broker replay buffer delivers it
    # immediately to the new subscriber.
    await app.state.project_event_broker.publish(
        pid, ProjectEvent(kind="task.created", payload={"id": "t1"})
    )

    cookies = {"taos_session": client.cookies.get("taos_session", "")}
    lines = await _collect_sse_lines(
        app, f"/api/projects/{pid}/events", cookies, n_lines=1, timeout=5.0
    )

    data_lines = [l for l in lines if l.startswith("data:")]
    assert data_lines, f"No data: lines received; got: {lines}"
    evt = json.loads(data_lines[0][5:].strip())
    assert evt["kind"] == "task.created"
    assert evt["payload"]["id"] == "t1"


@pytest.mark.asyncio
async def test_sse_heartbeat(app, client):
    """Heartbeat comment lines arrive after the 15 s queue timeout."""
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ph"})).json()["id"]
    cookies = {"taos_session": client.cookies.get("taos_session", "")}

    # Wait for a heartbeat line (starts with ':'). The route yields one after
    # 15 s with no events; give it 16 s to arrive.
    lines = await _collect_sse_lines(
        app, f"/api/projects/{pid}/events", cookies, n_lines=1, timeout=16.0
    )

    heartbeat_lines = [l for l in lines if l.startswith(":")]
    assert heartbeat_lines, f"No heartbeat lines received; got: {lines}"
