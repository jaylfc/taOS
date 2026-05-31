"""Tests for the agent debugger route module."""

import asyncio
import json

import pytest


async def _collect_sse_lines(app, path, cookies, n_lines, timeout=5.0):
    """Drive an ASGI SSE endpoint, collect the first n_lines non-empty lines,
    then cancel the request task.

    httpx's buffered ``client.get`` would block until the long-lived
    StreamingResponse closes, so we drive the ASGI app directly with a
    controlled receive/send and cancel once we have enough lines.
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
    done = asyncio.Event()
    status_code = {"value": None}

    async def receive():
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            status_code["value"] = message["status"]
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                for line in body.decode().split("\n"):
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

    return status_code["value"], lines


@pytest.mark.asyncio
async def test_debugger_ui_returns_html(client):
    """GET /agent/{agent_id}/debug returns an HTML page."""
    resp = await client.get("/agent/test-agent/debug")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "Agent Debugger" in body
    assert "debugger" in body.lower()


@pytest.mark.asyncio
async def test_trace_records_event(client):
    """POST /agent/{agent_id}/debug/trace records a trace event."""
    # The trace buffer is module-level and not reset between tests, so clear it
    # first to keep the exact-count assertion deterministic.
    await client.post("/agent/test-agent/debug/clear")
    resp = await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "tool_call", "data": {"tool": "search", "query": "test"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_status_returns_state(client):
    """GET /agent/{agent_id}/debug/status returns trace state."""
    # Record an event first
    await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "prompt", "data": {"text": "Hello"}},
    )
    resp = await client.get("/agent/test-agent/debug/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "test-agent"
    assert data["total_events"] >= 1
    assert "position" in data


@pytest.mark.asyncio
async def test_step_advances_position(client):
    """POST /agent/{agent_id}/debug/step advances the position."""
    await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "tool_call", "data": {"tool": "read"}},
    )
    await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "tool_result", "data": {"result": "ok"}},
    )

    resp = await client.post("/agent/test-agent/debug/step")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pos"] >= 1


@pytest.mark.asyncio
async def test_continue_jumps_to_end(client):
    """POST /agent/{agent_id}/debug/continue advances to end of trace."""
    for i in range(5):
        await client.post(
            "/agent/test-agent/debug/trace",
            json={"type": "log", "data": {"msg": f"step {i}"}},
        )

    resp = await client.post("/agent/test-agent/debug/continue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "continued"
    assert data["pos"] >= 5


@pytest.mark.asyncio
async def test_clear_removes_trace(client):
    """POST /agent/{agent_id}/debug/clear removes all trace data."""
    await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "tool_call", "data": {"tool": "exec"}},
    )
    resp = await client.post("/agent/test-agent/debug/clear")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"

    # Status should show 0 events
    status = await client.get("/agent/test-agent/debug/status")
    assert status.json()["total_events"] == 0


@pytest.mark.asyncio
async def test_trace_rejects_invalid_type(client):
    """POST trace rejects unknown event types."""
    resp = await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "garbage", "data": {}},
    )
    assert resp.status_code == 400
    assert "Unknown event type" in resp.json()["error"]


@pytest.mark.asyncio
async def test_trace_rejects_missing_type(client):
    """POST trace with missing type field gets 400."""
    resp = await client.post(
        "/agent/test-agent/debug/trace",
        json={"data": {}},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_separate_agents_have_separate_traces(client):
    """Each agent has its own trace."""
    await client.post(
        "/agent/alpha/debug/trace",
        json={"type": "log", "data": {"msg": "alpha event"}},
    )
    await client.post(
        "/agent/beta/debug/trace",
        json={"type": "log", "data": {"msg": "beta event"}},
    )

    alpha_status = await client.get("/agent/alpha/debug/status")
    beta_status = await client.get("/agent/beta/debug/status")

    assert alpha_status.json()["total_events"] == 1
    assert beta_status.json()["total_events"] == 1


@pytest.mark.asyncio
async def test_events_endpoint_returns_sse(app, client):
    """GET /agent/{agent_id}/debug/events streams an SSE frame."""
    await client.post("/agent/sse-agent/debug/clear")
    # Record an event first so there's data to stream
    await client.post(
        "/agent/sse-agent/debug/trace",
        json={"type": "tool_call", "data": {"tool": "test"}},
    )

    cookies = {"taos_session": client.cookies.get("taos_session", "")}
    status, lines = await _collect_sse_lines(
        app, "/agent/sse-agent/debug/events", cookies, n_lines=1, timeout=5.0
    )

    assert status == 200
    data_lines = [l for l in lines if l.startswith("data:")]
    assert data_lines, f"No data: lines received; got: {lines}"


@pytest.mark.asyncio
async def test_status_no_events(client):
    """GET status for an agent with no events."""
    resp = await client.get("/agent/new-agent/debug/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 0
    assert data["position"] == 0


@pytest.mark.asyncio
async def test_multiple_events_ordered(app, client):
    """Events are recorded and replayed in order."""
    await client.post("/agent/ordered-agent/debug/clear")
    for i in range(10):
        await client.post(
            "/agent/ordered-agent/debug/trace",
            json={"type": "log", "data": {"num": i}},
        )

    status = await client.get("/agent/ordered-agent/debug/status")
    assert status.json()["total_events"] == 10

    # Replaying the trace over SSE must yield the events in recorded order.
    # The stream emits one position frame followed by the 10 buffered logs, so
    # collect 11 data lines and check the log sequence.
    cookies = {"taos_session": client.cookies.get("taos_session", "")}
    _, lines = await _collect_sse_lines(
        app, "/agent/ordered-agent/debug/events", cookies, n_lines=11, timeout=5.0
    )

    nums = []
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = json.loads(line[len("data:"):].strip())
        if payload.get("type") == "log":
            nums.append(payload["data"]["num"])

    assert nums == list(range(10))
