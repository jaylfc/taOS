"""GET /api/device/v1/state (device bearer, scope agents:read).

Device-bearer only: returns the owner-filtered agent list for the paired
device, plus the server version, current time, and demo flag.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import tinyagentos
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from tinyagentos.agent_avatars import avatar_hash
from tinyagentos.device_auth import device_scope
from tinyagentos.device_scopes import AGENTS_READ
from tinyagentos.routes.auth import assemble_lock_agents, _demo_enabled

router = APIRouter()

logger = logging.getLogger(__name__)

_CAP_NAME = 48
_CAP_STATUS = 120
_CAP_LAST_RECAP = 180
_CAP_QUESTION = 280
_CAP_OPTION = 40
_ELLIPSIS = "\u2026"

# Poll and heartbeat intervals are module-level constants so tests can shrink
# them and advance the injectable clock without real sleeps.
_POLL_INTERVAL_S = 2.0
_HEARTBEAT_INTERVAL_S = 15.0


def _clock() -> float:
    """Injectable wall-clock for the SSE loop."""
    return time.time()


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = limit - len(_ELLIPSIS)
    if cut <= 0:
        return _ELLIPSIS[:limit]
    return text[:cut].rstrip() + _ELLIPSIS


def _hue_for(name: str) -> int:
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) % 360
    return h


async def _last_recap(agent_name: str, agent_messages) -> str:
    try:
        rows = await agent_messages.get_messages(agent_name, limit=1)
    except Exception:  # noqa: BLE001
        return ""
    if not rows:
        return ""
    row = rows[0]
    text = str(row.get("message") or "")
    return _cap(text, _CAP_LAST_RECAP)


def _decision_for_agent(agent: dict) -> dict | None:
    dec = agent.get("decision")
    if not dec:
        return None
    options = dec.get("options") or []
    capped = [_cap(str(o), _CAP_OPTION) for o in options if str(o).strip()]
    return {
        "id": str(dec.get("id") or ""),
        "question": _cap(str(dec.get("question") or ""), _CAP_QUESTION),
        "options": capped,
    }


async def _transform_agent(agent: dict, agent_messages) -> dict:
    original_name = agent.get("name") or ""
    name = _cap(str(original_name), _CAP_NAME)
    status = _cap(str(agent.get("status") or ""), _CAP_STATUS)
    framework = str(agent.get("framework") or "").lower()
    hue = _hue_for(original_name)
    ahash = avatar_hash(original_name)
    return {
        "name": name,
        "status": status,
        "framework": framework,
        "avatar": {
            "hue": hue,
            "hash": ahash,
        },
        "attention": bool(agent.get("attention")),
        "last_recap": await _last_recap(agent.get("name", ""), agent_messages),
        "decision": _decision_for_agent(agent),
    }


def _agent_change_key(agent: dict) -> tuple:
    av = agent.get("avatar") or {}
    return (
        agent.get("name"),
        agent.get("status"),
        agent.get("framework"),
        av.get("hue"),
        av.get("hash"),
        agent.get("attention"),
    )


# Global event counter and bounded history for Last-Event-ID resume.
_event_id = 0
_event_history: dict[int, str] = {}
_MAX_HISTORY = 200


def _record_event(eid: int, event_type: str) -> None:
    global _event_id, _event_history
    _event_id = eid
    _event_history[eid] = event_type
    # Prune history to bound memory.
    while len(_event_history) > _MAX_HISTORY:
        oldest = min(_event_history)
        del _event_history[oldest]


async def _events_stream(request: Request, device: dict):
    """Async generator that yields SSE frames for device events."""
    import sys
    print("[EVENTS_STREAM] STARTED", file=sys.stderr, flush=True)
    owner_id = device.get("user_id")
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    device_store = request.app.state.device_store
    agent_messages = getattr(request.app.state, "agent_messages", None)

    # Last-Event-ID handling.
    last_event_id_str = request.headers.get("last-event-id", "0").strip()
    try:
        last_event_id = int(last_event_id_str)
    except ValueError:
        last_event_id = 0

    need_snapshot = True
    if last_event_id > 0 and last_event_id in _event_history:
        need_snapshot = False

    print(f"[EVENTS_STREAM] about to call assemble_lock_agents", file=sys.stderr, flush=True)
    base_agents = await assemble_lock_agents(request, owner_id=owner_id)
    print(f"[EVENTS_STREAM] assemble_lock_agents returned {len(base_agents)} agents", file=sys.stderr, flush=True)
    transformed = [
        await _transform_agent(a, agent_messages)
        for a in base_agents
        if not a.get("system")
    ]
    print(f"[EVENTS_STREAM] transformed {len(transformed)} agents", file=sys.stderr, flush=True)
    snapshot_data = {
        "agents": transformed,
        "server": {
            "version": getattr(tinyagentos, "__version__", "unknown"),
        },
        "time": time.time(),
        "demo": _demo_enabled(request),
    }

    # Previous state keyed by agent name.
    prev_by_name: dict[str, dict] = {}
    prev_recap: dict[str, str] = {}
    prev_decision: dict[str, dict | None] = {}

    if need_snapshot:
        eid = _event_id + 1
        _record_event(eid, "snapshot")
        print(f"[EVENTS_STREAM] about to yield snapshot {eid}", file=sys.stderr, flush=True)
        yield f"id: {eid}\nevent: snapshot\ndata: {json.dumps(snapshot_data)}\n\n".encode("utf-8")
        print(f"[EVENTS_STREAM] yielded snapshot {eid}", file=sys.stderr, flush=True)

    # Emit agent.upsert for every current agent on connect so clients that
    # already received a snapshot still see a typed per-agent event.
    for a in transformed:
        prev_by_name[a["name"]] = a
        prev_recap[a["name"]] = a.get("last_recap", "") or ""
        prev_decision[a["name"]] = a.get("decision")
        eid = _event_id + 1
        _record_event(eid, "agent.upsert")
        yield f"id: {eid}\nevent: agent.upsert\ndata: {json.dumps(a)}\n\n".encode("utf-8")

    last_poll = _clock()
    last_heartbeat = _clock()

    while True:
        if await request.is_disconnected():
            print("[EVENTS_STREAM] disconnected", file=sys.stderr, flush=True)
            return

        now = _clock()

        # Re-check device token on every tick.
        if token:
            try:
                device_check = await device_store.get_by_token(token)
            except Exception:  # noqa: BLE001
                device_check = None
            if device_check is None:
                print("[EVENTS_STREAM] device revoked", file=sys.stderr, flush=True)
                return

        # Poll state at the configured interval.
        if now - last_poll >= _POLL_INTERVAL_S:
            last_poll = now

            demo_on = _demo_enabled(request)

            base_agents = await assemble_lock_agents(request, owner_id=owner_id)
            transformed = [
                await _transform_agent(a, agent_messages)
                for a in base_agents
                if not a.get("system")
            ]
            if not demo_on:
                transformed = [a for a in transformed if not a.get("demo")]

            current_by_name = {a["name"]: a for a in transformed}

            # Diff: agent.upsert and agent.remove.
            for name, agent in current_by_name.items():
                prev = prev_by_name.get(name)
                if prev is None:
                    eid = _event_id + 1
                    _record_event(eid, "agent.upsert")
                    yield f"id: {eid}\nevent: agent.upsert\ndata: {json.dumps(agent)}\n\n".encode("utf-8")
                else:
                    if _agent_change_key(agent) != _agent_change_key(prev):
                        eid = _event_id + 1
                        _record_event(eid, "agent.upsert")
                        yield f"id: {eid}\nevent: agent.upsert\ndata: {json.dumps(agent)}\n\n".encode("utf-8")

                # Recap change.
                current_recap = agent.get("last_recap", "") or ""
                if current_recap != prev_recap.get(name, ""):
                    eid = _event_id + 1
                    _record_event(eid, "agent.recap")
                    yield (
                        f"id: {eid}\nevent: agent.recap\n"
                        f"data: {json.dumps({'name': name, 'last_recap': current_recap})}\n\n"
                    ).encode("utf-8")

                # Decision open/close.
                current_dec = agent.get("decision")
                prev_dec = prev_decision.get(name)
                if current_dec and not prev_dec:
                    eid = _event_id + 1
                    _record_event(eid, "decision.open")
                    yield (
                        f"id: {eid}\nevent: decision.open\n"
                        f"data: {json.dumps({'name': name, 'decision': current_dec})}\n\n"
                    ).encode("utf-8")
                elif not current_dec and prev_dec:
                    eid = _event_id + 1
                    _record_event(eid, "decision.close")
                    yield f"id: {eid}\nevent: decision.close\ndata: {json.dumps({'name': name})}\n\n".encode("utf-8")

            # Removed agents.
            for name in prev_by_name:
                if name not in current_by_name:
                    eid = _event_id + 1
                    _record_event(eid, "agent.remove")
                    yield f"id: {eid}\nevent: agent.remove\ndata: {json.dumps({'name': name})}\n\n".encode("utf-8")

            prev_by_name = current_by_name
            prev_recap = {n: a.get("last_recap", "") or "" for n, a in current_by_name.items()}
            prev_decision = {n: a.get("decision") for n, a in current_by_name.items()}

        # Heartbeat.
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
            last_heartbeat = now
            eid = _event_id + 1
            _record_event(eid, "heartbeat")
            yield f"id: {eid}\n: ping\n\n".encode("utf-8")

        # Sleep in small steps so disconnect and device-check stay fresh.
        await asyncio.sleep(0.5)


@router.get("/api/device/v1/state")
async def device_state(request: Request, _device: dict = Depends(device_scope(AGENTS_READ))):
    try:
        owner_id = _device.get("user_id")
    except AttributeError:
        owner_id = None

    base_agents = await assemble_lock_agents(request, owner_id=owner_id)

    agent_messages = getattr(request.app.state, "agent_messages", None)

    transformed = [
        await _transform_agent(a, agent_messages)
        for a in base_agents
        if not a.get("system")
    ]

    return {
        "agents": transformed,
        "server": {
            "version": getattr(tinyagentos, "__version__", "unknown"),
        },
        "time": time.time(),
        "demo": _demo_enabled(request),
    }


@router.get("/api/device/v1/events")
async def device_events(request: Request, _device: dict = Depends(device_scope(AGENTS_READ))):
    return StreamingResponse(
        _events_stream(request, _device),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


