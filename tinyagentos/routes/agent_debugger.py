"""Agent debugger — step through tool calls and inspect results.

Provides an SSE-backed debugger UI that lets users step through an agent's
execution trace. Agents (or their adapters) push events via
POST /agent/{agent_id}/debug/trace; the UI subscribes via SSE and offers
step/continue controls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory trace store: {agent_id: [{"type": "tool_call"|"tool_result"|"prompt", ...}]}
_traces: dict[str, list[dict]] = defaultdict(list)

# Per-agent SSE queues so multiple listeners can subscribe
_queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

# Per-agent step position (index into trace)
_positions: dict[str, int] = {}

# Per-agent step events for blocking step/continue
_step_events: dict[str, asyncio.Event] = {}

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

_MAX_TRACE_LENGTH = 10_000  # cap per-agent trace to avoid unbounded memory growth


def _get_template(name: str) -> str:
    """Read an HTML template file, falling back to an error message."""
    path = _TEMPLATE_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return "<h1>Template not found</h1>"


def _trim_trace(agent_id: str) -> None:
    """Drop oldest events if the trace exceeds the cap."""
    events = _traces[agent_id]
    if len(events) > _MAX_TRACE_LENGTH:
        _traces[agent_id] = events[-_MAX_TRACE_LENGTH:]
        if _positions.get(agent_id, 0) < len(events) - _MAX_TRACE_LENGTH:
            _positions[agent_id] = max(0, len(_traces[agent_id]) - 1)


async def _broadcast(agent_id: str, event: dict) -> None:
    """Push an event to all SSE listeners for this agent."""
    payload = json.dumps(event)
    for q in _queues.get(agent_id, []):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


@router.get("/agent/{agent_id}/debug", response_class=HTMLResponse)
async def debugger_ui(agent_id: str) -> HTMLResponse:
    """Serve the debugger UI."""
    html = _get_template("agent_debugger.html")
    return HTMLResponse(html)


@router.get("/agent/{agent_id}/debug/events")
async def debugger_events(agent_id: str, request: Request):
    """SSE endpoint that streams trace events to the debugger UI."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)

    # Replay existing trace events
    for event in _traces.get(agent_id, []):
        queue.put_nowait(json.dumps(event))

    _queues[agent_id].append(queue)

    async def event_generator():
        try:
            # Send current position so the UI knows where to resume
            pos = _positions.get(agent_id, 0)
            yield f"data: {json.dumps({'type': 'position', 'pos': pos, 'total': len(_traces.get(agent_id, []))})}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _queues.get(agent_id, []):
                _queues[agent_id].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/agent/{agent_id}/debug/step")
async def debugger_step(agent_id: str) -> JSONResponse:
    """Advance the debugger one step.

    Steps the position forward by one event and broadcasts the new position.
    If the agent is blocked waiting for a step, signals it to continue.
    """
    events = _traces.get(agent_id, [])
    pos = _positions.get(agent_id, 0)

    if pos < len(events):
        _positions[agent_id] = pos + 1
        await _broadcast(agent_id, {
            "type": "position",
            "pos": pos + 1,
            "total": len(events),
        })

    # Signal any waiting agent
    step_event = _step_events.get(agent_id)
    if step_event:
        step_event.set()

    return JSONResponse({"status": "stepped", "pos": _positions.get(agent_id, 0)})


@router.post("/agent/{agent_id}/debug/continue")
async def debugger_continue(agent_id: str) -> JSONResponse:
    """Run until breakpoint or completion.

    Jumps to end of current trace and signals the agent to continue.
    """
    events = _traces.get(agent_id, [])
    _positions[agent_id] = len(events)
    await _broadcast(agent_id, {
        "type": "position",
        "pos": len(events),
        "total": len(events),
    })

    # Signal any waiting agent
    step_event = _step_events.get(agent_id)
    if step_event:
        step_event.set()

    return JSONResponse({"status": "continued", "pos": len(events)})


@router.post("/agent/{agent_id}/debug/trace")
async def debugger_trace(agent_id: str, request: Request) -> JSONResponse:
    """Record a trace event from the agent runtime.

    Agents (or adapters) POST here to push tool call / result / prompt events
    into the trace buffer. The SSE stream picks them up and broadcasts to
    connected debugger UIs.

    Request body: {"type": "tool_call"|"tool_result"|"prompt", ...}
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    event_type = body.get("type", "unknown")
    if event_type not in ("tool_call", "tool_result", "prompt", "error", "log"):
        return JSONResponse(
            {"error": f"Unknown event type: {event_type}"}, status_code=400
        )

    event = {
        "type": event_type,
        "ts": time.time(),
        "data": body.get("data", {}),
    }

    _traces[agent_id].append(event)
    _trim_trace(agent_id)

    await _broadcast(agent_id, event)

    # If in step mode, wait for user to step/continue
    step_event = _step_events.get(agent_id)
    if step_event:
        step_event.clear()
        await step_event.wait()

    return JSONResponse({"status": "recorded", "total": len(_traces[agent_id])})


@router.post("/agent/{agent_id}/debug/clear")
async def debugger_clear(agent_id: str) -> JSONResponse:
    """Clear the trace for an agent."""
    _traces.pop(agent_id, None)
    _positions.pop(agent_id, None)
    _step_events.pop(agent_id, None)
    await _broadcast(agent_id, {"type": "clear"})
    return JSONResponse({"status": "cleared"})


@router.get("/agent/{agent_id}/debug/status")
async def debugger_status(agent_id: str) -> JSONResponse:
    """Return current debugger state for the agent."""
    events = _traces.get(agent_id, [])
    return JSONResponse({
        "agent_id": agent_id,
        "total_events": len(events),
        "position": _positions.get(agent_id, 0),
        "has_listener": len(_queues.get(agent_id, [])) > 0,
    })
