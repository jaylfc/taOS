"""GET /api/device/v1/state (device bearer, scope agents:read).

Device-bearer only: returns the owner-filtered agent list for the paired
device, plus the server version, current time, and demo flag.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections import deque
from pathlib import Path

import tinyagentos
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from tinyagentos.agent_avatars import avatar_hash
from tinyagentos.atomic_io import atomic_write_text
from tinyagentos.device_auth import device_scope
from tinyagentos.device_scopes import AGENTS_READ, effective_scopes
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
_SLEEP_STEP_S = 0.5

# Per-owner shared event buffer for Last-Event-ID resume.
_owner_buffers: dict[str, dict] = {}
_BUFFER_LOCK = asyncio.Lock()
_MAX_HISTORY = 200

# Monotonically increasing epoch (ms since Unix epoch) so that IDs issued
# after a process restart are always larger than any ID from the previous
# process. Computed once at module import time.
_ID_EPOCH = int(time.time() * 1000)

_HWM_PATH: Path | None = None
_HWM_STEP = 1000


def seed_id_epoch(data_dir) -> None:
    global _ID_EPOCH, _HWM_PATH
    _HWM_PATH = Path(data_dir) / "device_event_hwm"
    stored = 0
    try:
        if _HWM_PATH.exists():
            stored = int(_HWM_PATH.read_text().strip())
    except (ValueError, OSError):
        stored = 0
    _ID_EPOCH = max(int(time.time() * 1000), stored + _HWM_STEP)
    # Atomically write _ID_EPOCH to the HWM file
    try:
        _HWM_PATH.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_HWM_PATH, str(_ID_EPOCH))
    except OSError:
        pass


def _get_owner_buffer(owner_id: str) -> dict:
    if owner_id not in _owner_buffers:
        _owner_buffers[owner_id] = {
            "next_id": _ID_EPOCH,
            "events": deque(maxlen=_MAX_HISTORY),
            "last_by_name": {},
        }
    return _owner_buffers[owner_id]


async def _device_still_authorized(device_store, token: str) -> bool:
    if not token:
        return True
    try:
        device_check = await device_store.get_by_token(token)
    except sqlite3.Error:
        return False
    if device_check is None:
        return False
    return AGENTS_READ in effective_scopes(device_check)


async def _emit_event(owner_id: str, event_type: str, data_json: str) -> int:
    async with _BUFFER_LOCK:
        buf = _get_owner_buffer(owner_id)
        eid = buf["next_id"]
        buf["next_id"] += 1
        buf["events"].append((eid, event_type, data_json))
        if _HWM_PATH is not None and eid % _HWM_STEP == 0:
            try:
                _HWM_PATH.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(_HWM_PATH, str(eid))
            except OSError:
                logger.warning("failed to write event hwm")
        return eid


async def _replay_and_baseline(owner_id: str, last_event_id: int) -> tuple[list, dict]:
    async with _BUFFER_LOCK:
        buf = _get_owner_buffer(owner_id)
        replay = [(eid, etype, data) for eid, etype, data in buf["events"] if eid > last_event_id]
        prev_by_name = dict(buf["last_by_name"])
        return replay, prev_by_name


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
    except sqlite3.Error:
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


async def _events_stream(request: Request, device: dict):
    """Async generator that yields SSE frames for device events."""
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

    base_agents = await assemble_lock_agents(request, owner_id=owner_id)
    transformed = [
        await _transform_agent(a, agent_messages)
        for a in base_agents
        if not a.get("system")
    ]
    snapshot_data = {
        "agents": transformed,
        "server": {
            "version": getattr(tinyagentos, "__version__", "unknown"),
        },
        "time": _clock(),
        "demo": _demo_enabled(request),
    }

    # Previous state keyed by agent name.
    prev_by_name: dict[str, dict] = {}
    prev_recap: dict[str, str] = {}
    prev_decision: dict[str, dict | None] = {}

    buf = _get_owner_buffer(owner_id)
    buffered_ids = [eid for eid, _, _ in buf["events"]]

    need_snapshot = False
    if last_event_id > 0:
        if not buffered_ids:
            need_snapshot = True
        else:
            oldest_id = min(buffered_ids)
            newest_id = max(buffered_ids)
            if last_event_id < oldest_id or last_event_id > newest_id:
                need_snapshot = True

    if need_snapshot:
        if not await _device_still_authorized(device_store, token):
            return
        data_json = json.dumps(snapshot_data)
        eid = await _emit_event(owner_id, "snapshot", data_json)
        yield f"id: {eid}\nevent: snapshot\ndata: {data_json}\n\n".encode("utf-8")
        for a in snapshot_data["agents"]:
            prev_by_name[a["name"]] = a
            prev_recap[a["name"]] = a.get("last_recap", "") or ""
            prev_decision[a["name"]] = a.get("decision")
        buf = _get_owner_buffer(owner_id)
        buf["last_by_name"] = dict(prev_by_name)
    elif last_event_id > 0:
        replay, prev_by_name = await _replay_and_baseline(owner_id, last_event_id)
        prev_recap = {n: a.get("last_recap", "") or "" for n, a in prev_by_name.items()}
        prev_decision = {n: a.get("decision") for n, a in prev_by_name.items()}
        for eid, event_type, data_json in replay:
            if not await _device_still_authorized(device_store, token):
                return
            yield f"id: {eid}\nevent: {event_type}\ndata: {data_json}\n\n".encode("utf-8")
    else:
        for a in transformed:
            prev_by_name[a["name"]] = a
            prev_recap[a["name"]] = a.get("last_recap", "") or ""
            prev_decision[a["name"]] = a.get("decision")
            data_json = json.dumps(a)
            eid = await _emit_event(owner_id, "agent.upsert", data_json)
            if not await _device_still_authorized(device_store, token):
                return
            yield f"id: {eid}\nevent: agent.upsert\ndata: {data_json}\n\n".encode("utf-8")
        buf = _get_owner_buffer(owner_id)
        buf["last_by_name"] = dict(prev_by_name)

    last_poll = _clock()
    last_heartbeat = _clock()

    while True:
        if await request.is_disconnected():
            return

        now = _clock()

        # Re-check device token on every tick.
        if not await _device_still_authorized(device_store, token):
            return

        # Poll state at the configured interval.
        if now - last_poll >= _POLL_INTERVAL_S:
            last_poll = now

            base_agents = await assemble_lock_agents(request, owner_id=owner_id)
            transformed = [
                await _transform_agent(a, agent_messages)
                for a in base_agents
                if not a.get("system")
            ]

            current_by_name = {a["name"]: a for a in transformed}

            # Diff: agent.upsert and agent.remove.
            for name, agent in current_by_name.items():
                prev = prev_by_name.get(name)
                if prev is None:
                    data_json = json.dumps(agent)
                    eid = await _emit_event(owner_id, "agent.upsert", data_json)
                    if not await _device_still_authorized(device_store, token):
                        return
                    yield f"id: {eid}\nevent: agent.upsert\ndata: {data_json}\n\n".encode("utf-8")
                else:
                    if _agent_change_key(agent) != _agent_change_key(prev):
                        data_json = json.dumps(agent)
                        eid = await _emit_event(owner_id, "agent.upsert", data_json)
                        if not await _device_still_authorized(device_store, token):
                            return
                        yield f"id: {eid}\nevent: agent.upsert\ndata: {data_json}\n\n".encode("utf-8")

                    # Recap change.
                    current_recap = agent.get("last_recap", "") or ""
                    if current_recap != prev_recap.get(name, ""):
                        data_json = json.dumps({"name": name, "last_recap": current_recap})
                        eid = await _emit_event(owner_id, "agent.recap", data_json)
                        if not await _device_still_authorized(device_store, token):
                            return
                        yield (
                            f"id: {eid}\nevent: agent.recap\n"
                            f"data: {data_json}\n\n"
                        ).encode("utf-8")

                    # Decision open/close.
                    current_dec = agent.get("decision")
                    prev_dec = prev_decision.get(name)
                    if current_dec and not prev_dec:
                        data_json = json.dumps({"name": name, "decision": current_dec})
                        eid = await _emit_event(owner_id, "decision.open", data_json)
                        if not await _device_still_authorized(device_store, token):
                            return
                        yield (
                            f"id: {eid}\nevent: decision.open\n"
                            f"data: {data_json}\n\n"
                        ).encode("utf-8")
                    elif not current_dec and prev_dec:
                        data_json = json.dumps({"name": name, "decision_id": prev_dec.get("id") or ""})
                        eid = await _emit_event(owner_id, "decision.close", data_json)
                        if not await _device_still_authorized(device_store, token):
                            return
                        yield f"id: {eid}\nevent: decision.close\ndata: {data_json}\n\n".encode("utf-8")
                    elif current_dec and prev_dec:
                        # Check if decision IDs differ
                        current_id = current_dec.get("id") or ""
                        prev_id = prev_dec.get("id") or ""
                        if current_id != prev_id:
                            # Emit decision.close for old id
                            data_json = json.dumps({"name": name, "decision_id": prev_id})
                            eid = await _emit_event(owner_id, "decision.close", data_json)
                            if not await _device_still_authorized(device_store, token):
                                return
                            yield f"id: {eid}\nevent: decision.close\ndata: {data_json}\n\n".encode("utf-8")
                            # Emit decision.open for new id
                            data_json = json.dumps({"name": name, "decision": current_dec})
                            eid = await _emit_event(owner_id, "decision.open", data_json)
                            if not await _device_still_authorized(device_store, token):
                                return
                            yield (
                                f"id: {eid}\nevent: decision.open\n"
                                f"data: {data_json}\n\n"
                            ).encode("utf-8")

            # Removed agents.
            for name in prev_by_name:
                if name not in current_by_name:
                    data_json = json.dumps({"name": name})
                    eid = await _emit_event(owner_id, "agent.remove", data_json)
                    if not await _device_still_authorized(device_store, token):
                        return
                    yield f"id: {eid}\nevent: agent.remove\ndata: {data_json}\n\n".encode("utf-8")

            prev_by_name = current_by_name
            prev_recap = {n: a.get("last_recap", "") or "" for n, a in current_by_name.items()}
            prev_decision = {n: a.get("decision") for n, a in current_by_name.items()}
            buf = _get_owner_buffer(owner_id)
            buf["last_by_name"] = dict(current_by_name)

        # Heartbeat.
        if now - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
            last_heartbeat = now
            yield ": ping\n\n".encode("utf-8")

        # Sleep in small steps so disconnect and device-check stay fresh.
        await asyncio.sleep(_SLEEP_STEP_S)


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
        "time": _clock(),
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
