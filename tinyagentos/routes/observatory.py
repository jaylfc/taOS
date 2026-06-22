"""Observatory: queue-control (pause) for the agent dispatch fleet.

The "steer" half of the Observatory spec. A controller-owned pause flag that
the owl dispatch loop (and off-box lanes) read each iteration to decide whether
to dispatch new work. Global pause is the panic button; per-lane pause lets one
lane drain while another keeps going. State is a JSON file in data_dir so it
survives controller restarts and is not a local tmux concern.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user

router = APIRouter()

_DEFAULT_STATE: dict = {"global": False, "lanes": {}}


def _state_path(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "observatory_pause.json"


def _read_state(request: Request) -> dict:
    p = _state_path(request)
    if not p.exists():
        return {"global": False, "lanes": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"global": False, "lanes": {}}
    # Normalise shape so a hand-edited or partial file cannot break readers.
    return {
        "global": bool(data.get("global", False)),
        "lanes": {str(k): bool(v) for k, v in (data.get("lanes") or {}).items() if v},
    }


def _write_state(request: Request, state: dict) -> None:
    p = _state_path(request)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))


class PauseBody(BaseModel):
    scope: str  # "global" or a lane handle (e.g. "@taOS-dev-kilo-owl-alpha")
    paused: bool


@router.get("/api/observatory/pause")
async def get_pause(request: Request, user: CurrentUser = Depends(current_user)):
    """Current pause state. Any authenticated caller (and the dispatch loop,
    which polls this each iteration) may read it."""
    return _read_state(request)


@router.post("/api/observatory/pause")
async def set_pause(body: PauseBody, request: Request, user: CurrentUser = Depends(current_user)):
    """Pause or resume the queue globally or for a single lane. Admin only,
    since it steers the whole fleet."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    scope = body.scope.strip()
    if not scope:
        return JSONResponse({"error": "scope required"}, status_code=400)
    state = _read_state(request)
    if scope == "global":
        state["global"] = body.paused
    elif body.paused:
        state["lanes"][scope] = True
    else:
        state["lanes"].pop(scope, None)
    _write_state(request, state)
    return state
