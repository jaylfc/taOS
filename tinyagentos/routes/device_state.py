"""GET /api/device/v1/state (device bearer, scope agents:read).

Device-bearer only: returns the owner-filtered agent list for the paired
device, plus the server version, current time, and demo flag.
"""
from __future__ import annotations

import tinyagentos
import time

from fastapi import APIRouter, Depends, HTTPException, Request

from tinyagentos.agent_avatars import avatar_hash
from tinyagentos.device_auth import device_scope
from tinyagentos.device_scopes import AGENTS_READ
from tinyagentos.routes.auth import assemble_lock_agents, _demo_enabled

router = APIRouter()

_CAP_NAME = 48
_CAP_STATUS = 120
_CAP_LAST_RECAP = 180
_CAP_QUESTION = 280
_CAP_OPTION = 40
_ELLIPSIS = "\u2026"


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
        "options": capped[:4],
    }


async def _transform_agent(agent: dict, agent_messages) -> dict:
    name = _cap(str(agent.get("name") or ""), _CAP_NAME)
    status = _cap(str(agent.get("status") or ""), _CAP_STATUS)
    framework = str(agent.get("framework") or "").lower()
    hue = _hue_for(name)
    ahash = avatar_hash(name)
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
