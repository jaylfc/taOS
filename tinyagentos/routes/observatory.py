"""Observatory: queue-control (pause) for the agent dispatch fleet.

The "steer" half of the Observatory spec. A controller-owned pause flag that
the owl dispatch loop (and off-box lanes) read each iteration to decide whether
to dispatch new work. Global pause is the panic button; per-lane pause lets one
lane drain while another keeps going. State is a JSON file in data_dir so it
survives controller restarts and is not a local tmux concern.
"""

from __future__ import annotations

import json
import os
import tempfile
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
    # Write to a temp file in the same dir then atomically rename, so a crash
    # mid-write or a concurrent writer can never leave a truncated/corrupt file
    # (a reader always sees either the old or the new complete state).
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".observatory_pause.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(state))
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


@router.get("/api/observatory/fleet")
async def get_fleet(request: Request, user: CurrentUser = Depends(current_user)):
    """The Observe half: which agents are working and what they hold right now.

    Derives state from the board: an agent that holds a claimed task is
    'working' on it. Admins see every project; other users see their own.
    The current pause state is returned alongside so the UI can show both in
    one read. Trace-timeline and PR-in-review enrichment are phase 2.
    """
    pstore = request.app.state.project_store
    tstore = request.app.state.project_task_store
    if user.is_admin:
        projects = await pstore.list_projects(status=None)
    else:
        projects = await pstore.list_for_user(user.user_id, status=None)

    agents: list[dict] = []
    for proj in projects:
        pid = proj.get("id")
        if not pid:
            continue
        for t in await tstore.list_tasks(pid, status="claimed"):
            handle = t.get("claimed_by")
            if not handle:
                continue
            agents.append({
                "handle": handle,
                "state": "working",
                "holds": {
                    "task_id": t.get("id"),
                    "project_id": pid,
                    "title": t.get("title"),
                },
            })
    agents.sort(key=lambda a: a["handle"])
    return {"agents": agents, "paused": _read_state(request)}
