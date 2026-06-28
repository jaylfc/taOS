"""Observatory: queue-control (pause) for the agent dispatch fleet.

The "steer" half of the Observatory spec. A controller-owned pause flag that
the owl dispatch loop (and off-box lanes) read each iteration to decide whether
to dispatch new work. Global pause is the panic button; per-lane pause lets one
lane drain while another keeps going. State is a JSON file in data_dir so it
survives controller restarts and is not a local tmux concern.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user

router = APIRouter()

_DEFAULT_STATE: dict = {"global": False, "lanes": {}}

# A working agent that has held its claimed card longer than this (seconds) is
# flagged ``stale`` in the fleet view: it catches a hung or wedged lane the pause
# switch would otherwise hide. Board-only signal (claim age); a richer
# no-trace-progress check is phase 2 once the lane->trace-slug mapping is wired.
STALE_CLAIM_SECONDS = 1800

# Serialise read-modify-write of the pause/throttle state files so two
# concurrent admin POSTs cannot lose an update (each reads the same prior
# state and the second os.replace clobbers the first). The writes are
# infrequent admin actions, so a single in-process lock is sufficient.
_write_lock = asyncio.Lock()


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


def _atomic_write(p: Path, state: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same dir then atomically rename, so a crash
    # mid-write or a concurrent writer can never leave a truncated/corrupt file
    # (a reader always sees either the old or the new complete state).
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix="." + p.stem + ".", suffix=".tmp")
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


def _write_state(request: Request, state: dict) -> None:
    _atomic_write(_state_path(request), state)


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
    async with _write_lock:
        state = _read_state(request)
        if scope == "global":
            state["global"] = body.paused
        elif body.paused:
            state["lanes"][scope] = True
        else:
            state["lanes"].pop(scope, None)
        _write_state(request, state)
    return state


# --- Throttle dials (decision 3: concurrency). A per-lane (and global) cap on
# how many cards a lane may have in flight at once, which the dispatch loop
# reads each iteration as its MAX_OPEN_PRS. null/absent = no override (the
# loop's built-in default applies). Pause is the on/off switch; throttle is the
# volume knob. ---


def _throttle_path(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "observatory_throttle.json"


def _coerce_limit(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _read_throttle(request: Request) -> dict:
    p = _throttle_path(request)
    if not p.exists():
        return {"global": None, "lanes": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"global": None, "lanes": {}}
    lanes = {}
    for k, v in (data.get("lanes") or {}).items():
        lim = _coerce_limit(v)
        if lim is not None:
            lanes[str(k)] = lim
    return {"global": _coerce_limit(data.get("global")), "lanes": lanes}


class ThrottleBody(BaseModel):
    scope: str  # "global" or a lane handle
    max_concurrent: int | None = None  # None or <= 0 clears the cap


@router.get("/api/observatory/throttle")
async def get_throttle(request: Request, user: CurrentUser = Depends(current_user)):
    """Current concurrency caps. The dispatch loop polls this each iteration."""
    return _read_throttle(request)


@router.post("/api/observatory/throttle")
async def set_throttle(body: ThrottleBody, request: Request, user: CurrentUser = Depends(current_user)):
    """Set or clear a concurrency cap globally or for a single lane. Admin only."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    scope = body.scope.strip()
    if not scope:
        return JSONResponse({"error": "scope required"}, status_code=400)
    limit = _coerce_limit(body.max_concurrent)
    async with _write_lock:
        state = _read_throttle(request)
        if scope == "global":
            state["global"] = limit
        elif limit is not None:
            state["lanes"][scope] = limit
        else:
            state["lanes"].pop(scope, None)
        _atomic_write(_throttle_path(request), state)
    return state


@router.get("/api/observatory/fleet")
async def get_fleet(request: Request, user: CurrentUser = Depends(current_user)):
    """The Observe half: which agents are working and what they hold right now.

    Derives state from the board: an agent that holds a claimed task is
    'working' on it, and a registered agent that holds none is 'idle'. Admins
    see every project + agent; other users see their own. The current pause
    state is returned alongside so the UI can show both in one read.
    Trace-timeline and PR-in-review enrichment are phase 2.
    """
    pstore = request.app.state.project_store
    tstore = request.app.state.project_task_store
    if user.is_admin:
        projects = await pstore.list_projects(status=None)
    else:
        projects = await pstore.list_for_user(user.user_id, status=None)

    now = time.time()
    agents: list[dict] = []
    working: set[str] = set()
    for proj in projects:
        pid = proj.get("id")
        if not pid:
            continue
        for t in await tstore.list_tasks(pid, status="claimed"):
            handle = t.get("claimed_by")
            if not handle:
                continue
            working.add(handle)
            # Claim age drives the stale badge. claimed_at is a Unix epoch set on
            # claim. Use `is not None` so an epoch-0 timestamp is not mistaken for
            # missing, and clamp to >= 0 so clock skew (claimed_at slightly in the
            # future) cannot report a negative age.
            claimed_at = t.get("claimed_at")
            held_seconds = (
                max(0, int(now - claimed_at)) if claimed_at is not None else None
            )
            agents.append({
                "handle": handle,
                "state": "working",
                "holds": {
                    "task_id": t.get("id"),
                    "project_id": pid,
                    "title": t.get("title"),
                },
                "held_seconds": held_seconds,
                "stale": held_seconds is not None and held_seconds >= STALE_CLAIM_SECONDS,
            })

    # Registered agents holding no card are idle; surface them so the fleet
    # shows the full active roster, not just the busy lanes. Best-effort: a
    # missing or erroring registry must not break the working view.
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is not None:
        try:
            if user.is_admin:
                registered = await registry.list_all(status="active")
            else:
                registered = await registry.list_for_user(user.user_id, status="active")
        except Exception:
            registered = []
        for rec in registered:
            handle = (rec.get("handle") or "").strip()
            if not handle or handle in working:
                continue
            working.add(handle)
            # Idle agents hold no card, so no claim age; keep the shape uniform.
            agents.append({
                "handle": handle, "state": "idle", "holds": None,
                "held_seconds": None, "stale": False,
            })

    agents.sort(key=lambda a: a["handle"])
    return {"agents": agents, "paused": _read_state(request)}
