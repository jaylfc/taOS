from __future__ import annotations

import logging
import re
import time as _time

import asyncio as _asyncio
import json as _json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from tinyagentos.agent_token_auth import (
    PROJECT_SCOPE_MISMATCH_DETAIL,
    check_agent_scope_for_project,
)
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin
from tinyagentos.projects.folders import ensure_project_layout, write_project_yaml

logger = logging.getLogger(__name__)
router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class CreateProjectIn(BaseModel):
    name: str
    slug: str
    description: str = ""
    settings: dict = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, v: str) -> str:
        if not _SLUG_RE.fullmatch(v):
            raise ValueError("slug must match ^[a-z0-9][a-z0-9_-]{0,62}$")
        return v


class UpdateProjectIn(BaseModel):
    name: str | None = None
    description: str | None = None
    settings: dict | None = None


def _mirror(request: Request, project: dict) -> None:
    # Folder mirror is best-effort: if the disk write fails (permissions, full
    # filesystem, transient I/O), the DB row is still authoritative and the
    # request should succeed. Failures are logged for operator visibility.
    try:
        root = request.app.state.projects_root
        ensure_project_layout(root, project["slug"], project["name"])
        write_project_yaml(root, project["slug"], project)
    except Exception as exc:
        logger.warning(
            "project folder mirror failed for slug=%s: %s", project.get("slug"), exc
        )


def _beads_mark_dirty(request: Request, project_id: str) -> None:
    """Best-effort hand-off to the Beads bridge. Never raises."""
    bridge = getattr(request.app.state, "beads_bridge", None)
    if bridge is None:
        return
    try:
        bridge.mark_dirty(project_id)
    except Exception:
        logger.warning(
            "beads mark_dirty failed for project %s", project_id, exc_info=True
        )


@router.post("/api/projects")
async def create_project(
    payload: CreateProjectIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    try:
        p = await store.create_project(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            settings=payload.settings,
            created_by=user.user_id,
            user_id=user.user_id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    await store.log_activity(p["id"], user.user_id, "project.created", {"slug": p["slug"]})
    _mirror(request, p)
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel
        await ensure_a2a_channel(
            request.app.state.chat_channels,
            request.app.state.project_store,
            p["id"],
            config=getattr(request.app.state, "config", None),
        )
    except Exception:
        logger.warning("a2a ensure failed for new project %s", p.get("id"), exc_info=True)
    return p


@router.get("/api/projects")
async def list_projects(
    request: Request,
    status: str | None = "active",
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    if user.is_admin:
        items = await store.list_projects(status=status)
    else:
        items = await store.list_for_user(user.user_id, status=status)
    return {"items": items}


@router.get("/api/projects/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not user.is_admin and user.user_id != p["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    return p


@router.patch("/api/projects/{project_id}")
async def update_project(
    project_id: str,
    payload: UpdateProjectIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, p["user_id"])
    await store.update_project(
        project_id,
        name=payload.name,
        description=payload.description,
        settings=payload.settings,
    )
    p = await store.get_project(project_id)
    await store.log_activity(project_id, user.user_id, "project.updated", payload.model_dump(exclude_none=True))
    _mirror(request, p)
    return p


@router.post("/api/projects/{project_id}/archive")
async def archive_project(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, p["user_id"])
    await store.set_status(project_id, "archived")
    p = await store.get_project(project_id)
    await store.log_activity(project_id, user.user_id, "project.archived", {})
    return p


@router.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    project = await store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])

    await store.set_status(project_id, "deleted")
    await store.log_activity(project_id, user.user_id, "project.deleted", {})

    # Archive scoped chat channels
    channels = request.app.state.chat_channels
    for ch in await channels.list_channels(project_id=project_id):
        await channels.set_settings(ch["id"], {"archived": True})

    # Tombstone the folder rather than deleting on disk (recoverable). Disk
    # rename is best-effort; failure shouldn't block the DB tombstone.
    try:
        root = request.app.state.projects_root
        src = root / project["slug"]
        if src.exists():
            ts = int(_time.time())
            dest = root / f"{project['slug']}.deleted-{ts}"
            src.rename(dest)
    except Exception as exc:
        logger.warning(
            "project folder tombstone failed for slug=%s: %s", project.get("slug"), exc
        )

    return await store.get_project(project_id)


class AddMemberIn(BaseModel):
    mode: str  # "native" | "clone"
    agent_id: str | None = None
    source_agent_id: str | None = None
    clone_memory: bool = True
    role: str = "member"


@router.post("/api/projects/{project_id}/members")
async def add_member(
    project_id: str,
    payload: AddMemberIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    project = await store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])

    if payload.mode == "native":
        if not payload.agent_id:
            return JSONResponse({"error": "agent_id required"}, status_code=400)
        member_id = payload.agent_id
        member_kind = "native"
        source_agent_id = None
        memory_seed = "none"
    elif payload.mode == "clone":
        if not payload.source_agent_id:
            return JSONResponse({"error": "source_agent_id required"}, status_code=400)
        member_id = f"{payload.source_agent_id}-{project['slug']}"
        member_kind = "clone"
        source_agent_id = payload.source_agent_id
        memory_seed = "snapshot" if payload.clone_memory else "empty"
    else:
        return JSONResponse({"error": "mode must be native|clone"}, status_code=400)

    await store.add_member(
        project_id=project_id,
        member_id=member_id,
        member_kind=member_kind,
        role=payload.role,
        source_agent_id=source_agent_id,
        memory_seed=memory_seed,
    )
    await store.log_activity(
        project_id, user.user_id, "member.added",
        {"member_id": member_id, "kind": member_kind, "memory_seed": memory_seed},
    )
    members = await store.list_members(project_id)
    _mirror(request, {**project, "members": members})
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel
        await ensure_a2a_channel(
            request.app.state.chat_channels,
            request.app.state.project_store,
            project_id,
            config=getattr(request.app.state, "config", None),
        )
    except Exception:
        logger.warning("a2a ensure failed for project %s on add_member", project_id, exc_info=True)
    return next(m for m in members if m["member_id"] == member_id)


@router.get("/api/projects/{project_id}/members")
async def list_members(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not user.is_admin and user.user_id != p["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"items": await store.list_members(project_id)}


class LeadIn(BaseModel):
    is_lead: bool


@router.patch("/api/projects/{project_id}/members/{member_id}/lead")
async def set_lead(
    project_id: str,
    member_id: str,
    body: LeadIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, p["user_id"])
    try:
        await store.set_member_lead(project_id, member_id, body.is_lead)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel
        await ensure_a2a_channel(
            request.app.state.chat_channels,
            store,
            project_id,
            config=getattr(request.app.state, "config", None),
        )
    except Exception:
        logger.warning("a2a ensure failed for project %s on set_lead", project_id, exc_info=True)
    return {"ok": True, "is_lead": body.is_lead}


@router.delete("/api/projects/{project_id}/members/{member_id}")
async def remove_member(
    project_id: str,
    member_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    project = await store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    await store.remove_member(project_id, member_id)
    await store.log_activity(project_id, user.user_id, "member.removed", {"member_id": member_id})
    members = await store.list_members(project_id)
    _mirror(request, {**project, "members": members})
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel
        await ensure_a2a_channel(
            request.app.state.chat_channels,
            request.app.state.project_store,
            project_id,
            config=getattr(request.app.state, "config", None),
        )
    except Exception:
        logger.warning("a2a ensure failed for project %s on remove_member", project_id, exc_info=True)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Task models
# ---------------------------------------------------------------------------

class CreateTaskIn(BaseModel):
    title: str
    body: str = ""
    priority: int = 0
    labels: list[str] = Field(default_factory=list)
    assignee_id: str | None = None
    parent_task_id: str | None = None


class UpdateTaskIn(BaseModel):
    title: str | None = None
    body: str | None = None
    priority: int | None = None
    labels: list[str] | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None


class ClaimIn(BaseModel):
    claimer_id: str


class ReleaseIn(BaseModel):
    releaser_id: str


class CloseIn(BaseModel):
    closed_by: str
    reason: str | None = None


class ReopenIn(BaseModel):
    reopened_by: str | None = None


class AddRelIn(BaseModel):
    to_task_id: str
    kind: str


# ---------------------------------------------------------------------------
# Task route helpers
# ---------------------------------------------------------------------------

async def _get_owned_project(
    pstore, project_id: str, user: CurrentUser
) -> "dict | JSONResponse":
    """Fetch a project and apply existence-hiding 404 for non-owners."""
    p = await pstore.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not user.is_admin and user.user_id != p["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    return p


async def _require_task_in_project(
    store, project_id: str, task_id: str
) -> "dict | JSONResponse":
    task = await store.get_task(task_id)
    if task is None or task["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return task


async def _authorize_task_actor(
    request: Request, pstore, project_id: str
) -> "tuple[str, bool, dict] | JSONResponse":
    """Resolve the actor for a task route that accepts EITHER a session
    owner/admin OR an approved external agent's registry JWT (scope
    project_tasks) bound to THIS project.

    Returns ``(actor_id, is_agent, project)`` on success, or a JSONResponse to
    return directly.  These routes take ``request: Request`` and auth INSIDE the
    handler (mirroring routes/a2a_bus.py) because ``Depends(current_user)`` would
    401 an agent that has no session before the handler runs.

    Security:
      * A session non-owner gets the existence-hiding 404 from _get_owned_project.
      * An agent token bound to a DIFFERENT project is collapsed into the SAME
        existence-hiding 404, so it is indistinguishable from a non-owner and
        never confirms another project's existence (invariant 1).
      * A malformed / inactive / missing-scope token keeps its 401/403.
    """
    uid = getattr(request.state, "user_id", None)
    if uid:
        user = CurrentUser(
            user_id=uid, is_admin=bool(getattr(request.state, "is_admin", False))
        )
        project_or_err = await _get_owned_project(pstore, project_id, user)
        if isinstance(project_or_err, JSONResponse):
            return project_or_err
        return (user.user_id, False, project_or_err)

    try:
        caller = await check_agent_scope_for_project(
            request, "project_tasks", project_id
        )
    except HTTPException as exc:
        if exc.status_code == 403 and exc.detail == PROJECT_SCOPE_MISMATCH_DETAIL:
            return JSONResponse({"error": "not found"}, status_code=404)
        raise
    if caller is None:
        # No session and no Bearer token: fail closed with existence-hiding 404.
        return JSONResponse({"error": "not found"}, status_code=404)
    project = await pstore.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return (caller, True, project)


def _resolve_actor(
    is_agent: bool, actor_id: str, body_actor: "str | None"
) -> "str | None | JSONResponse":
    """Bind a lifecycle actor id to the verified token when the caller is an agent.

    Invariant 3: an agent must act only as itself.  If a lifecycle body names an
    actor id (claimer_id / releaser_id / closed_by / reopened_by) that differs
    from the token's canonical_id, reject 403; otherwise the token id is
    authoritative.  A session caller keeps its provided actor id unchanged (an
    owner/admin may still record an action on behalf of any worker id)."""
    if is_agent:
        if body_actor and body_actor != actor_id:
            return JSONResponse(
                {"error": "agent may only act as itself"}, status_code=403
            )
        return actor_id
    return body_actor


# ---------------------------------------------------------------------------
# Task routes — order matters: /tasks/ready before /tasks/{task_id}
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project_id}/tasks")
async def create_task(
    project_id: str,
    payload: CreateTaskIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_task_store
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    if payload.parent_task_id is not None:
        parent = await store.get_task(payload.parent_task_id)
        if parent is None or parent["project_id"] != project_id:
            return JSONResponse({"error": "invalid parent_task_id"}, status_code=400)
    t = await store.create_task(
        project_id=project_id,
        title=payload.title,
        body=payload.body,
        priority=payload.priority,
        labels=payload.labels,
        assignee_id=payload.assignee_id,
        parent_task_id=payload.parent_task_id,
        created_by=user.user_id,
    )
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, user.user_id, "task.created", {"task_id": t["id"], "title": t["title"]})
    return t


@router.get("/api/projects/{project_id}/tasks")
async def list_tasks(
    project_id: str,
    request: Request,
    status: str | None = None,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    return {"items": await store.list_tasks(project_id=project_id, status=status)}


@router.get("/api/projects/{project_id}/tasks/ready")
async def ready_tasks(
    project_id: str,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    return {"items": await store.list_ready_tasks(project_id=project_id)}


@router.get("/api/projects/{project_id}/tasks/{task_id}")
async def get_task(
    project_id: str,
    task_id: str,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    t = await store.get_task(task_id)
    if t is None or t["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return t


@router.patch("/api/projects/{project_id}/tasks/{task_id}")
async def update_task(
    project_id: str,
    task_id: str,
    payload: UpdateTaskIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    # Session owner/admin only. A project_tasks agent drives its board through
    # read + the lifecycle actions (claim/release/close/reopen) + comments; free
    # PATCH of title/body/assignee_id/parent is a broader mutation than that
    # scope grants, so it stays off the agent allowlist (Kilo review on #1774).
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)

    if payload.parent_task_id is not None:
        if payload.parent_task_id == task_id:
            return JSONResponse({"error": "cycle: cannot self-parent"}, status_code=400)
        parent = await store.get_task(payload.parent_task_id)
        if parent is None or parent["project_id"] != project_id:
            return JSONResponse({"error": "parent not in this project"}, status_code=400)
        # walk ancestors to detect indirect cycles (parent's chain must not reach task_id)
        seen = {task_id, parent["id"]}
        cur = parent
        while cur is not None and cur.get("parent_task_id"):
            if cur["parent_task_id"] in seen:
                return JSONResponse({"error": "cycle in parent chain"}, status_code=400)
            seen.add(cur["parent_task_id"])
            cur = await store.get_task(cur["parent_task_id"])

    await store.update_task(task_id, **payload.model_dump(exclude_none=True))
    _beads_mark_dirty(request, project_id)
    return await store.get_task(task_id)


@router.post("/api/projects/{project_id}/tasks/{task_id}/claim")
async def claim_task(
    project_id: str,
    task_id: str,
    payload: ClaimIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, _project = auth
    claimer_id = _resolve_actor(is_agent, actor_id, payload.claimer_id)
    if isinstance(claimer_id, JSONResponse):
        return claimer_id
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.claim_task(task_id, claimer_id)
    if not ok:
        # Distinguish "task taken by someone else" from "you already hold an
        # active task" so the agent knows to finish or release it first.
        held = await store.held_task(claimer_id)
        if held is not None and held != task_id:
            return JSONResponse(
                {
                    "error": "agent already holds an active task",
                    "held_task": held,
                    "detail": "complete or release your current task before claiming another",
                },
                status_code=409,
            )
        return JSONResponse({"error": "already claimed"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, claimer_id, "task.claimed", {"task_id": task_id})
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        await notifs.emit_event("task.claimed", "Task claimed", f"{task_id} claimed by {claimer_id}")
    return await store.get_task(task_id)


@router.post("/api/projects/{project_id}/tasks/{task_id}/release")
async def release_task(
    project_id: str,
    task_id: str,
    payload: ReleaseIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, _project = auth
    releaser_id = _resolve_actor(is_agent, actor_id, payload.releaser_id)
    if isinstance(releaser_id, JSONResponse):
        return releaser_id
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.release_task(task_id, releaser_id)
    if not ok:
        return JSONResponse({"error": "not claimed by releaser"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    return await store.get_task(task_id)


@router.post("/api/projects/{project_id}/tasks/{task_id}/close")
async def close_task(
    project_id: str,
    task_id: str,
    payload: CloseIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, project = auth
    closed_by = _resolve_actor(is_agent, actor_id, payload.closed_by)
    if isinstance(closed_by, JSONResponse):
        return closed_by
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.close_task(task_id, closed_by=closed_by, reason=payload.reason)
    if not ok:
        return JSONResponse({"error": "cannot close"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, closed_by, "task.closed", {"task_id": task_id})
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        await notifs.emit_event("task.closed", "Task closed", f"{task_id} closed by {closed_by}")
    task = await store.get_task(task_id)
    qmd = getattr(request.app.state, "qmd_client", None)
    if qmd is not None and task is not None:
        try:
            from tinyagentos.projects.lifecycle import index_closed_task
            await index_closed_task(qmd, project, task)
        except Exception:
            await pstore.log_activity(
                project_id, closed_by, "task.qmd_index_failed", {"task_id": task_id}
            )
    return task


@router.post("/api/projects/{project_id}/tasks/{task_id}/reopen")
async def reopen_task(
    project_id: str,
    task_id: str,
    request: Request,
    payload: ReopenIn | None = None,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, _project = auth
    body_actor = payload.reopened_by if payload and payload.reopened_by else None
    resolved = _resolve_actor(is_agent, actor_id, body_actor)
    if isinstance(resolved, JSONResponse):
        return resolved
    actor = resolved or "user"
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.reopen_task(task_id, reopened_by=actor)
    if not ok:
        return JSONResponse({"error": "task is not closed"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, actor, "task.reopened", {"task_id": task_id})
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        await notifs.emit_event("task.reopened", "Task reopened", f"{task_id} reopened by {actor}")
    return await store.get_task(task_id)


@router.get("/api/projects/{project_id}/audit")
async def project_audit_feed(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
    limit: int = 100,
):
    """Project-wide board activity feed, newest first (owner-gated, #105).

    Scoped to the project so it never surfaces another project's events. limit
    is clamped to a sane ceiling to keep the response bounded.
    """
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    audit = getattr(request.app.state, "board_audit", None)
    capped = max(1, min(limit, 500))
    events = await audit.recent_for_project(project_id, capped) if audit is not None else []
    return {"project_id": project_id, "events": events}


@router.get("/api/projects/{project_id}/tasks/{task_id}/audit")
async def task_audit_history(
    project_id: str,
    task_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Append-only audit trail for a task: every status transition in order (#105)."""
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    audit = getattr(request.app.state, "board_audit", None)
    events = await audit.history(task_id) if audit is not None else []
    return {"task_id": task_id, "events": events}


@router.get("/api/projects/tasks/{task_id}/context")
async def task_context(
    task_id: str,
    request: Request,
):
    """Relational context for a task: its goal (project + ancestry) and
    blockers. Project-agnostic path — task_id alone resolves the project
    for the ownership check, since task ids are globally unique."""
    store = request.app.state.project_task_store
    task = await store.get_task(task_id)
    if task is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, task["project_id"])
    if isinstance(auth, JSONResponse):
        return auth
    return await store.get_task_context(task_id)


@router.post("/api/projects/{project_id}/tasks/{task_id}/relationships")
async def add_relationship(
    project_id: str,
    task_id: str,
    payload: AddRelIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    try:
        rel = await store.add_relationship(
            project_id=project_id,
            from_task_id=task_id,
            to_task_id=payload.to_task_id,
            kind=payload.kind,
            created_by=user.user_id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _beads_mark_dirty(request, project_id)
    return rel


class AddCommentIn(BaseModel):
    body: str
    # Optional: an agent caller may omit it and the route pins it to the token
    # canonical_id. A session caller must still supply it (route enforces).
    author_id: str | None = None
    replies_to_comment_id: str | None = None


@router.post("/api/projects/{project_id}/tasks/{task_id}/comments")
async def add_comment(
    project_id: str,
    task_id: str,
    payload: AddCommentIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, _project = auth
    # Invariant 3: an agent authors a comment only as itself. A comment author is
    # an actor id just like the lifecycle fields, so bind it to the token id and
    # 403 a mismatched body value (a session owner keeps its provided author_id).
    author_id = _resolve_actor(is_agent, actor_id, payload.author_id)
    if isinstance(author_id, JSONResponse):
        return author_id
    if author_id is None:
        return JSONResponse({"error": "author_id required"}, status_code=400)
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    try:
        return await store.add_comment(
            task_id=task_id,
            author_id=author_id,
            body=payload.body,
            replies_to_comment_id=payload.replies_to_comment_id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/projects/{project_id}/tasks/{task_id}/comments")
async def list_comments(
    project_id: str,
    task_id: str,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    return {"items": await store.list_comments(task_id)}


@router.get("/api/projects/{project_id}/tasks/{task_id}/relationships")
async def list_relationships(
    project_id: str,
    task_id: str,
    request: Request,
    direction: str = "from",
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    return {"items": await store.list_relationships(task_id, direction=direction)}


@router.get("/api/projects/{project_id}/activity")
async def activity_feed(
    project_id: str,
    request: Request,
    limit: int = 100,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not user.is_admin and user.user_id != p["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"items": await store.list_activity(project_id, limit=limit)}


@router.get("/api/projects/{project_id}/memory/search")
async def memory_search(
    project_id: str,
    request: Request,
    q: str,
    limit: int = 10,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_store
    project_or_err = await _get_owned_project(store, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    project = project_or_err
    qmd = getattr(request.app.state, "qmd_client", None)
    if qmd is None:
        return {"items": []}
    items = await qmd.search(
        q,
        collection=f"project-{project['slug']}",
        tags=[f"project:{project_id}"],
        limit=limit,
    )
    return {"items": items}


@router.get("/api/projects/{project_id}/events")
async def project_events(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    broker = request.app.state.project_event_broker
    queue = await broker.subscribe(project_id)

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await _asyncio.wait_for(queue.get(), timeout=15.0)
                    payload = {"kind": ev.kind, "payload": ev.payload, "ts": ev.ts}
                    yield f"data: {_json.dumps(payload)}\n\n"
                except _asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            await broker.unsubscribe(project_id, queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/projects/{project_id}/beads/export")
async def beads_export(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Force a synchronous render of the project's .beads/tasks.jsonl
    snapshot. Returns 503 when the bridge isn't running."""
    bridge = getattr(request.app.state, "beads_bridge", None)
    if bridge is None:
        return JSONResponse({"error": "beads bridge not running"}, status_code=503)
    pstore = request.app.state.project_store
    project_or_err = await _get_owned_project(pstore, project_id, user)
    if isinstance(project_or_err, JSONResponse):
        return project_or_err
    path = await bridge.export_now(project_id)
    if path is None:
        return JSONResponse({"error": "export failed"}, status_code=500)
    return {"path": str(path)}
