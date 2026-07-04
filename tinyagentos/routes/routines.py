from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.projects.routine_runner import fire_routine
from tinyagentos.rate_limit import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter()

# Abuse guard for the unauthenticated webhook trigger. Keyed per token, this
# caps how fast a single routine's webhook can mass-create tasks: a small burst
# then a steady trickle. Unknown/disabled tokens 404 before this is consulted,
# so they cost nothing here. In-process (resets on restart) — the right
# trade-off for a self-hosted single-process controller; front with Caddy/nginx
# for cross-process limits.
_webhook_limiter = RateLimiter(capacity=5, refill_per_second=0.1)


async def _get_owned_project(
    pstore, project_id: str, user: CurrentUser
) -> "dict | JSONResponse":
    """Fetch a project and apply existence-hiding 404 for non-owners.

    Mirrors tinyagentos/routes/projects.py's helper of the same name.
    """
    p = await pstore.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not user.is_admin and user.user_id != p["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    return p


async def _require_routine_in_project(store, project_id: str, routine_id: str) -> "dict | JSONResponse":
    routine = await store.get_routine(routine_id)
    if routine is None or routine["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return routine


class CreateRoutineIn(BaseModel):
    title: str
    body_template: str = ""
    assignee_id: str | None = None
    trigger_kind: str = "cron"
    cron_expr: str | None = None
    enabled: bool = True


class UpdateRoutineIn(BaseModel):
    title: str | None = None
    body_template: str | None = None
    assignee_id: str | None = None
    cron_expr: str | None = None
    enabled: bool | None = None


@router.post("/api/projects/{project_id}/routines")
async def create_routine(
    project_id: str,
    payload: CreateRoutineIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.routine_store
    try:
        return await store.create_routine(
            project_id=project_id,
            title=payload.title,
            created_by=user.user_id,
            body_template=payload.body_template,
            assignee_id=payload.assignee_id,
            trigger_kind=payload.trigger_kind,
            cron_expr=payload.cron_expr,
            enabled=payload.enabled,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/projects/{project_id}/routines")
async def list_routines(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.routine_store
    return {"items": await store.list_routines(project_id)}


@router.patch("/api/projects/{project_id}/routines/{rid}")
async def update_routine(
    project_id: str,
    rid: str,
    payload: UpdateRoutineIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.routine_store
    guard = await _require_routine_in_project(store, project_id, rid)
    if isinstance(guard, JSONResponse):
        return guard
    try:
        return await store.update_routine(rid, **payload.model_dump(exclude_none=True))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.delete("/api/projects/{project_id}/routines/{rid}")
async def delete_routine(
    project_id: str,
    rid: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.routine_store
    guard = await _require_routine_in_project(store, project_id, rid)
    if isinstance(guard, JSONResponse):
        return guard
    await store.delete_routine(rid)
    return {"ok": True}


@router.post("/api/projects/{project_id}/routines/{rid}/trigger")
async def trigger_routine(
    project_id: str,
    rid: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Manual/API trigger: fires the routine immediately regardless of its
    schedule. Session-authenticated, project-owner-gated."""
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.routine_store
    guard = await _require_routine_in_project(store, project_id, rid)
    if isinstance(guard, JSONResponse):
        return guard
    task = await fire_routine(request.app.state, guard)
    return {"ok": True, "task": task}


@router.post("/api/webhooks/routines/{token}")
async def webhook_trigger_routine(token: str, request: Request):
    """Inbound webhook trigger — no session auth. Authenticated solely by the
    per-routine opaque token embedded in the URL, matched via an indexed unique
    lookup in RoutineStore.get_by_webhook_token. Unknown or disabled tokens 404
    rather than distinguishing the reason, so the endpoint never leaks which
    routines exist. A per-token rate limit caps task-creation spam."""
    if not _webhook_limiter.check(token):
        return JSONResponse({"error": "rate limited"}, status_code=429)
    store = request.app.state.routine_store
    routine = await store.get_by_webhook_token(token)
    if routine is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    task = await fire_routine(request.app.state, routine)
    return {"ok": True, "task": task}
