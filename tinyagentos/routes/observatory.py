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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_token_auth import (
    _get_grants_store,
    _grant_unexpired,
    check_agent_scope,
)

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


async def _authorize_observatory_read(request: Request) -> str:
    """Gate an observatory read request.

    Admin (session cookie or local token) is allowed unconditionally -- the
    middleware has already set request.state.is_admin for those.  Otherwise the
    caller must present a registry JWT holding an active ``observatory_control``
    grant; check_agent_scope raises 401 (bad/malformed token) or 403 (valid token
    but not active / missing scope) and returns None only when no Bearer header
    is present, which is rejected here as 403 (fail closed).
    """
    if getattr(request.state, "is_admin", False):
        return "admin"
    caller = await check_agent_scope(request, "observatory_control")
    if caller is None:
        raise HTTPException(status_code=403, detail="forbidden")
    request.state.agent_caller = caller
    return caller


async def _authorize_observatory_write(request: Request) -> str:
    """Gate an observatory write request.

    Admin (session cookie or local token) is allowed unconditionally.  Otherwise
    the caller must present a registry JWT holding an active GLOBAL
    (null-project) ``observatory_control`` grant -- a project-bound grant must
    not confer fleet-wide pause/throttle.  check_agent_scope raises 401/403; a
    missing Bearer header returns None and is rejected here as 403 (fail closed).
    """
    if getattr(request.state, "is_admin", False):
        return "admin"
    caller = await check_agent_scope(request, "observatory_control")
    if caller is None:
        raise HTTPException(status_code=403, detail="forbidden")
    grants_store = _get_grants_store(request)
    grants = await grants_store.list_grants(caller)
    now = datetime.now(timezone.utc)
    has_global = any(
        g["scope"] == "observatory_control"
        and g.get("project_id") is None
        and _grant_unexpired(g.get("expires_at"), now)
        for g in grants
    )
    if not has_global:
        raise HTTPException(status_code=403, detail="forbidden")
    return caller


class PauseBody(BaseModel):
    scope: str  # "global" or a lane handle (e.g. "@taOS-dev-kilo-owl-alpha")
    paused: bool


@router.get("/api/observatory/pause")
async def get_pause(request: Request):
    """Current pause state. Admin or an agent holding ``observatory_control`` may read it."""
    await _authorize_observatory_read(request)
    return _read_state(request)


@router.post("/api/observatory/pause")
async def set_pause(body: PauseBody, request: Request):
    """Pause or resume the queue globally or for a single lane. Admin or an
    agent holding a GLOBAL ``observatory_control`` grant only, since it steers
    the whole fleet."""
    await _authorize_observatory_write(request)
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
async def get_throttle(request: Request):
    """Current concurrency caps. Admin or an agent holding ``observatory_control`` may read it."""
    await _authorize_observatory_read(request)
    return _read_throttle(request)


@router.post("/api/observatory/throttle")
async def set_throttle(body: ThrottleBody, request: Request):
    """Set or clear a concurrency cap globally or for a single lane. Admin or an
    agent holding a GLOBAL ``observatory_control`` grant only."""
    await _authorize_observatory_write(request)
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


# --- Per-session approval-mode steer (#133). A controller-owned mode the
# dispatch loop will read each iteration to decide how much an agent may do
# without asking: ``default`` (ask before edits), ``accept_edits`` (auto-allow
# workspace/tmp edits), ``dont_ask`` (no prompts). Approval mode is PER-SESSION
# (the scope key is a session id), unlike pause/throttle which are per-lane: how
# much an agent may do without asking is a property of the running session, not
# the lane. Same storage shape as pause/throttle (global plus a per-session
# override map, JSON in data_dir, admin-gated writes). This is
# the storage+API layer only; wiring the dispatch loop to honour the mode and
# the Observatory UI control are a deliberate follow-up (held for Jay). Until
# then nothing reads this, so it changes no live agent behaviour. ---

APPROVAL_MODES = ("default", "accept_edits", "dont_ask")


def _approval_path(request: Request) -> Path:
    return Path(request.app.state.data_dir) / "observatory_approval_mode.json"


def _coerce_mode(v) -> str | None:
    """Return *v* if it is a recognised mode, else None."""
    return v if v in APPROVAL_MODES else None


def _read_approval(request: Request) -> dict:
    """Current approval modes. ``global`` falls back to the safe ``default``
    (ask before edits); only valid non-default per-session overrides are kept so
    a hand-edited or partial file cannot widen permissions unexpectedly."""
    p = _approval_path(request)
    if not p.exists():
        return {"global": "default", "sessions": {}}
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"global": "default", "sessions": {}}
    # A hand-edited file could be valid JSON but not the expected shape (a scalar,
    # a list, or a non-dict "sessions"). Guard both so a malformed file degrades
    # to the safe default instead of 500ing the GET.
    if not isinstance(data, dict):
        return {"global": "default", "sessions": {}}
    raw_sessions = data.get("sessions")
    if not isinstance(raw_sessions, dict):
        raw_sessions = {}
    sessions = {}
    for k, v in raw_sessions.items():
        mode = _coerce_mode(v)
        if mode is not None and mode != "default":
            sessions[str(k)] = mode
    return {"global": _coerce_mode(data.get("global")) or "default", "sessions": sessions}


class ApprovalModeBody(BaseModel):
    scope: str  # "global" or a session id
    mode: str  # one of APPROVAL_MODES; "default" clears a per-session override


@router.get("/api/observatory/approval-mode")
async def get_approval_mode(request: Request):
    """Current approval modes. Admin or an agent holding ``observatory_control`` may read it."""
    await _authorize_observatory_read(request)
    return _read_approval(request)


@router.post("/api/observatory/approval-mode")
async def set_approval_mode(body: ApprovalModeBody, request: Request):
    """Set the approval mode globally or for a single session. Admin or an
    agent holding a GLOBAL ``observatory_control`` grant only, since it relaxes
    how much an agent may do without asking."""
    await _authorize_observatory_write(request)
    scope = body.scope.strip()
    if not scope:
        return JSONResponse({"error": "scope required"}, status_code=400)
    mode = _coerce_mode(body.mode)
    if mode is None:
        return JSONResponse(
            {"error": f"invalid mode; expected one of {list(APPROVAL_MODES)}"},
            status_code=400,
        )
    async with _write_lock:
        state = _read_approval(request)
        if scope == "global":
            state["global"] = mode
        elif mode != "default":
            state["sessions"][scope] = mode
        else:
            state["sessions"].pop(scope, None)
        _atomic_write(_approval_path(request), state)
    return state


@router.get("/api/observatory/fleet")
async def get_fleet(request: Request):
    """The Observe half: which agents are working and what they hold right now.

    Derives state from the board: an agent that holds a claimed task is
    'working' on it, and a registered agent that holds none is 'idle'. Admins
    and agents holding a GLOBAL ``observatory_control`` grant see every project
    + agent; project-scoped agents see their own granted projects. The current
    pause state is returned alongside so the UI can show both in one read.
    Trace-timeline and PR-in-review enrichment are phase 2.
    """
    await _authorize_observatory_read(request)
    is_admin = getattr(request.state, "is_admin", False)
    caller = getattr(request.state, "agent_caller", None)
    user_id = getattr(request.state, "user_id", None)

    grants_store = _get_grants_store(request)
    caller_has_global = False
    caller_project_ids: set[str] = set()
    if caller and not is_admin:
        grants = await grants_store.list_grants(caller)
        now = datetime.now(timezone.utc)
        caller_has_global = any(
            g["scope"] == "observatory_control"
            and g.get("project_id") is None
            and _grant_unexpired(g.get("expires_at"), now)
            for g in grants
        )
        caller_project_ids = {
            g["project_id"] for g in grants
            if g["scope"] == "observatory_control"
            and g.get("project_id") is not None
            and _grant_unexpired(g.get("expires_at"), now)
        }

    pstore = request.app.state.project_store
    tstore = request.app.state.project_task_store
    if is_admin or caller_has_global:
        projects = await pstore.list_projects(status=None)
    elif caller_project_ids:
        all_projects = await pstore.list_projects(status=None)
        projects = [p for p in all_projects if p.get("id") in caller_project_ids]
    else:
        projects = await pstore.list_for_user(user_id, status=None) if user_id else []

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
                "framework": "",  # backfilled from the registry below, if known
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
    registered: list[dict] = []
    if registry is not None:
        try:
            if is_admin or caller_has_global:
                registered = await registry.list_all(status="active")
            else:
                registered = await registry.list_for_user(user_id, status="active") if user_id else []
        except Exception:
            registered = []
        for rec in registered:
            handle = (rec.get("handle") or "").strip()
            if not handle or handle in working:
                continue
            working.add(handle)
            # Idle agents hold no card, so no claim age; keep the shape uniform.
            agents.append({
                "handle": handle, "state": "idle",
                "framework": (rec.get("framework") or ""),
                "holds": None, "held_seconds": None, "stale": False,
            })

    # Lane badge: attach each agent's framework (kilo/opencode/hermes/...) from the
    # registry. Working agents come from the board (claim handle), so backfill them
    # from the registry by handle; unknown handles keep the empty default.
    framework_by_handle = {
        (rec.get("handle") or "").strip(): (rec.get("framework") or "")
        for rec in registered
        if (rec.get("handle") or "").strip()
    }
    for a in agents:
        if not a.get("framework"):
            a["framework"] = framework_by_handle.get(a["handle"], "")

    agents.sort(key=lambda a: a["handle"])

    # Fleet health summary: a single-glance roll-up for the Observatory header so
    # the UI does not have to recompute counts. `status` is degraded when any
    # lane is stale (a wedged claim the pause switch would hide), else active if
    # anything is working, else idle.
    stale_handles = [a["handle"] for a in agents if a.get("stale")]
    working_count = sum(1 for a in agents if a["state"] == "working")
    idle_count = sum(1 for a in agents if a["state"] == "idle")
    health = {
        "total": len(agents),
        "working": working_count,
        "idle": idle_count,
        "stale": len(stale_handles),
        "stale_handles": stale_handles,
        "status": "degraded" if stale_handles else ("active" if working_count else "idle"),
    }
    return {"agents": agents, "paused": _read_state(request), "health": health}
