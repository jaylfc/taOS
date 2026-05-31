"""Tests for the agent debugger route module."""

import pytest


@pytest.fixture(autouse=True)
def _reset_debugger_state():
    """Reset module-level debugger globals around every test.

    The debugger uses process-global dicts for traces, queues, positions and
    step events. Without an explicit reset, state leaks across tests (and into
    other test modules in a full-suite run), which can leave a connected SSE
    queue or an unset step event behind and make a later /trace POST block.
    """
    from tinyagentos.routes import agent_debugger as dbg

    dbg._traces.clear()
    dbg._queues.clear()
    dbg._positions.clear()
    dbg._step_events.clear()
    yield
    dbg._traces.clear()
    dbg._queues.clear()
    dbg._positions.clear()
    dbg._step_events.clear()


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
    # Reset shared module-level state for deterministic test
    from tinyagentos.routes.agent_debugger import _traces, _positions, _step_events
    _traces.clear()
    _positions.clear()
    _step_events.clear()

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
async def test_events_endpoint_returns_sse(client):
    """GET /agent/{agent_id}/debug/events yields an SSE stream and terminates.

    The SSE generator runs an infinite ``while True`` loop, so it cannot be
    consumed over httpx's buffering ASGI transport without hanging. Drive the
    handler directly with a Request that reports a client disconnect, so the
    generator yields its initial position frame and then exits cleanly — this
    proves frame delivery AND that the stream terminates (no leaked task).
    """
    import asyncio

    from tinyagentos.routes.agent_debugger import debugger_events

    # Record an event first so there's trace data to replay.
    await client.post(
        "/agent/test-agent/debug/trace",
        json={"type": "tool_call", "data": {"tool": "test"}},
    )

    class _DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    resp = await debugger_events("test-agent", _DisconnectedRequest())  # type: ignore[arg-type]

    frames: list = []

    async def _drain():
        async for frame in resp.body_iterator:
            frames.append(frame)

    # Bound the drain so a regression that breaks termination fails loudly
    # instead of hanging.
    await asyncio.wait_for(_drain(), timeout=5)

    assert frames, "SSE stream returned no data"
    assert "position" in frames[0], frames[0]


@pytest.mark.asyncio
async def test_status_no_events(client):
    """GET status for an agent with no events."""
    resp = await client.get("/agent/new-agent/debug/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 0
    assert data["position"] == 0


@pytest.mark.asyncio
async def test_multiple_events_ordered(client):
    """Events are recorded in order."""
    await client.post(
        "/agent/test-agent/debug/clear",
    )
    for i in range(10):
        await client.post(
            "/agent/test-agent/debug/trace",
            json={"type": "log", "data": {"num": i}},
        )

    status = await client.get("/agent/test-agent/debug/status")
    assert status.json()["total_events"] == 10

    # Verify ordering against the recorded trace store. The /status endpoint
    # only returns counts (not the events), so assert against _traces directly
    # — otherwise the ordering check is dead code that never runs.
    from tinyagentos.routes.agent_debugger import _traces
    nums = [e["data"]["num"] for e in _traces.get("test-agent", []) if "num" in e.get("data", {})]
    assert nums == list(range(10)), f"events not in insertion order: {nums}"
