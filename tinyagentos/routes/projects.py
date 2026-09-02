from __future__ import annotations

import logging
import re
import time as _time
import uuid

import asyncio as _asyncio
import json as _json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tinyagentos.agent_token_auth import (
    PROJECT_SCOPE_MISMATCH_DETAIL,
    check_agent_project_grants,
    check_agent_scope_for_project,
)
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin
from tinyagentos.projects.folders import (
    ensure_element_folder,
    ensure_project_layout,
    write_project_yaml,
)
from tinyagentos.projects.project_store import ProjectConflict
from tinyagentos.projects.task_store import _ELEMENT_CLEAR

logger = logging.getLogger(__name__)
router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

# The documented task status enum surfaced by the kanban read endpoints.  The
# store itself is more permissive internally (it also tracks ``cancelled`` and
# ``quarantined``), but the READ/aggregate API surface only advertises these
# three and must reject anything else rather than silently filter to an empty
# list (which would hide a caller's typo).
_TASK_STATUS_ENUM = ("open", "claimed", "closed")


async def _is_field_free(store, field: str, value: str) -> bool:
    """Return True when no project uses ``value`` for ``field``."""
    if field == "name":
        return await store.get_project_by_name(value) is None
    return await store.get_project_by_slug(value) is None


async def _free_suggestions(store, field: str, taken: str) -> list[str]:
    """Generate 2-3 free suggestions for a collided name or slug.

    Each candidate is verified against the store so it is genuinely free.
    Formats: numeric suffix (<value>-2), prefixed (<prefix>-<value>),
    and short random suffix (<value>-<shortid>).
    """
    suggestions: list[str] = []

    # 1. Numeric suffix: <value>-2, <value>-3, ...
    for i in range(2, 10):
        cand = f"{taken}-{i}"
        if await _is_field_free(store, field, cand):
            suggestions.append(cand)
            break

    # 2. Prefixed: <prefix>-<value>
    for prefix in ("team", "new", "app"):
        cand = f"{prefix}-{taken}"
        if await _is_field_free(store, field, cand):
            suggestions.append(cand)
            break

    # 3. Short random suffix: <value>-<shortid>
    for _ in range(20):
        shortid = uuid.uuid4().hex[:5]
        cand = f"{taken}-{shortid}"
        if await _is_field_free(store, field, cand):
            suggestions.append(cand)
            break

    return suggestions


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
    except ProjectConflict as e:
        suggestions = await _free_suggestions(store, e.field, e.taken)
        return JSONResponse(
            {
                "error": str(e),
                "field": e.field,
                "taken": e.taken,
                "suggestions": suggestions,
            },
            status_code=409,
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
    mode: str  # "native" | "clone" | "human"
    agent_id: str | None = None
    source_agent_id: str | None = None
    clone_memory: bool = True
    role: str = "member"
    contact_id: str | None = None  # for mode="human": the contact's hub id


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
    elif payload.mode == "human":
        if not payload.contact_id:
            return JSONResponse({"error": "contact_id required for human members"}, status_code=400)
        member_id = payload.contact_id
        member_kind = "human"
        source_agent_id = None
        memory_seed = "none"
    else:
        return JSONResponse({"error": "mode must be native|clone|human"}, status_code=400)

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


class ProjectLeadIn(BaseModel):
    member_id: "str | None" = None


@router.patch("/api/projects/{project_id}/lead")
async def set_project_lead(
    project_id: str,
    body: ProjectLeadIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Set the project's exclusive Lead (D7). The single lead_member_id pointer
    makes the one-lead-per-project invariant structural: setting a new lead
    atomically unsets the previous one. ``member_id: null`` clears the lead.

    Session-only (owner or admin, same gate as the members routes). A member id
    not in the project returns 404.
    """
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, p["user_id"])
    try:
        await store.set_lead(project_id, body.member_id)
    except KeyError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    await store.log_activity(
        project_id, user.user_id, "project.lead_changed", {"member_id": body.member_id}
    )
    members = await store.list_members(project_id)
    _mirror(request, {**p, "members": members})
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
    return {"ok": True, "lead_member_id": body.member_id}


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

class _TaskRequestModelMixin:
    """Shared base for task request models (tsk-kqzpjt).

    Observation phase: unknown request-body keys are silently accepted
    (extra="allow") but logged as a single warning so bad clients can be
    detected before a future flip to extra="forbid". No behaviour change for
    any caller; every currently-working request keeps working.
    """

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def _log_unknown_keys(self):
        extra = self.model_extra
        if extra:
            # Bound the logged key list: this validator runs before the
            # handler's auth, so key names are attacker-chosen input. Cap
            # count and per-key length to keep a junk body from amplifying
            # into the logs.
            keys = sorted(extra.keys())
            shown = [k[:50] for k in keys[:10]]
            if len(keys) > 10:
                shown.append(f"...and {len(keys) - 10} more")
            logger.warning(
                "%s received unknown keys %s; valid fields are %s",
                type(self).__name__,
                shown,
                sorted(type(self).model_fields.keys()),
            )
        return self


class CreateTaskIn(_TaskRequestModelMixin, BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body: str = ""
    priority: int = 0
    labels: list[str] = Field(default_factory=list)
    assignee_id: str | None = None
    parent_task_id: str | None = None
    element_id: str | None = None

    @model_validator(mode="after")
    def _assert_body_for_claimable(self) -> CreateTaskIn:
        labels = self.labels
        has_claimable = any(l.strip().lower() == "claimable" for l in labels)
        if has_claimable and not self.body.strip():
            raise ValueError(
                "Claimable cards must have a non-empty body"
            )
        return self


class UpdateTaskIn(_TaskRequestModelMixin, BaseModel):
    title: str | None = None
    body: str | None = None
    priority: int | None = None
    labels: list[str] | None = None
    status: str | None = None
    assignee_id: str | None = None
    parent_task_id: str | None = None
    # Omit to leave the element tag unchanged; send "none" to clear it to
    # project-level (NULL); send a real element id to move the task.
    element_id: str | None = None


class ClaimIn(_TaskRequestModelMixin, BaseModel):
    claimer_id: str


class ReleaseIn(_TaskRequestModelMixin, BaseModel):
    releaser_id: str


class CloseIn(_TaskRequestModelMixin, BaseModel):
    closed_by: str
    reason: str | None = None


class ReopenIn(_TaskRequestModelMixin, BaseModel):
    reopened_by: str | None = None


class AddRelIn(_TaskRequestModelMixin, BaseModel):
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
    request: Request,
    pstore,
    project_id: str,
    scope: str = "project_tasks",
    agent_grants: "dict[str, dict] | None" = None,
    agent_canonical_id: "str | None" = None,
) -> "tuple[str, bool, dict] | JSONResponse":
    """Resolve the actor for a task route that accepts EITHER a session
    owner/admin OR an approved external agent's registry JWT holding ``scope``
    (default ``project_tasks``) bound to THIS project.

    ``scope`` is a parameter because authoring uses a SEPARATE, narrower grant
    (``project_tasks_create``): project_tasks is documented and tested as read
    plus lifecycle plus comments, so creation must not ride on it.

    ``agent_grants`` / ``agent_canonical_id`` are an optional fast path for the
    cross-project aggregate: the caller resolves the agent's identity + grant
    map ONCE (via ``check_agent_project_grants``) and passes the ``{project_id:
    grant}`` lookup here so the per-project authorization is an O(1) dict
    membership instead of re-verifying the token and re-fetching
    ``list_grants`` for every project (an O(K) grant-store N+1).  The security
    invariants are unchanged -- membership in the pre-built map is exactly the
    same active-grant-bound-to-project predicate ``check_agent_scope_for_project``
    enforces, and the identity/active/rotation checks already ran once upstream.

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

    if agent_grants is not None:
        # Grant-aware fast path (aggregate): the caller already verified the
        # agent and built the active project_tasks -> grant map once.  A missing
        # entry means "no active grant bound to this project" -> same
        # existence-hiding 404 as the slow path, so it never confirms a
        # project the agent is not entitled to.
        if agent_canonical_id is None:
            raise ValueError("canonical_id required with agent_grants")
        if project_id not in agent_grants:
            return JSONResponse({"error": "not found"}, status_code=404)
        project = await pstore.get_project(project_id)
        if project is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return (agent_canonical_id, True, project)

    try:
        caller = await check_agent_scope_for_project(
            request, scope, project_id
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


async def _authorize_project_lead(
    request: Request, pstore, project_id: str
) -> "tuple[str, bool, dict] | JSONResponse":
    """Authorize a LEAD-only curation action (mark a card claimable).

    Accepts EITHER a session owner/admin OR the project's LEAD agent's registry
    JWT (scope ``project_tasks`` bound to THIS project AND the agent is this
    project's ``lead_member_id``).  This is deliberately narrower than
    ``_authorize_task_actor``: a plain ``project_tasks`` agent (a worker lane)
    can claim/work cards but may NOT curate the board.  Only the lead flags
    which cards the fleet may pick up.

    Mirrors ``_authorize_task_actor``'s existence-hiding 404 for every refusal
    (non-owner session, wrong-project token, or a non-lead agent) so a caller
    can never distinguish "exists but I am not the lead" from "does not exist".
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
        return JSONResponse({"error": "not found"}, status_code=404)
    project = await pstore.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Curation is lead-only: a non-lead agent (even one holding project_tasks)
    # is collapsed into the same existence-hiding 404 as a non-owner session.
    if project.get("lead_member_id") != caller:
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


async def _require_active_element(
    estore, project_id: str, element_id: str
) -> "dict | JSONResponse":
    """Validate that element_id names a non-archived element of this project.

    Returns the element dict on success, or a 400 JSONResponse when the element
    is missing, belongs to a different project, or has been archived. Archived
    elements keep their tags but are no longer valid targets for new tagging.
    """
    el = await estore.get_element(element_id)
    if el is None or el["project_id"] != project_id:
        return JSONResponse({"error": "element not in project"}, status_code=400)
    if el.get("archived_at") is not None:
        return JSONResponse({"error": "element archived"}, status_code=400)
    return el


# ---------------------------------------------------------------------------
# Task routes — order matters: /tasks/ready before /tasks/{task_id}
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project_id}/tasks")
async def create_task(
    project_id: str,
    payload: CreateTaskIn,
    request: Request,
):
    """Create a task as a session owner/admin, or as an approved external agent
    holding ``project_tasks_create`` on THIS project.

    Authoring is a SEPARATE scope from ``project_tasks`` on purpose: that scope
    is documented and tested as read + lifecycle + comments ("Invariant 2 + 5"),
    so widening it would retroactively grant authoring to every agent already
    approved for it. Existence-hiding 404 behaviour matches the other task
    routes.
    """
    store = request.app.state.project_task_store
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    actor_or_err = await _authorize_task_actor(
        request, pstore, project_id, scope="project_tasks_create"
    )
    if isinstance(actor_or_err, JSONResponse):
        return actor_or_err
    actor_id, _is_agent, _project = actor_or_err
    if payload.parent_task_id is not None:
        parent = await store.get_task(payload.parent_task_id)
        if parent is None or parent["project_id"] != project_id:
            return JSONResponse({"error": "invalid parent_task_id"}, status_code=400)
    element_id = payload.element_id
    if element_id is not None:
        el_check = await _require_active_element(estore, project_id, element_id)
        if isinstance(el_check, JSONResponse):
            return el_check
    t = await store.create_task(
        project_id=project_id,
        title=payload.title,
        body=payload.body,
        priority=payload.priority,
        labels=payload.labels,
        assignee_id=payload.assignee_id,
        parent_task_id=payload.parent_task_id,
        element_id=element_id,
        created_by=actor_id,
    )
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, actor_id, "task.created", {"task_id": t["id"], "title": t["title"]})
    return t


@router.get("/api/projects/{project_id}/tasks")
async def list_tasks(
    project_id: str,
    request: Request,
    status: str | None = None,
    element_id: str | None = None,
):
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    return {"items": await store.list_tasks(project_id=project_id, status=status, element_id=element_id)}


async def _caller_project_candidates(
    request: Request, pstore
) -> "tuple[list[dict], dict[str, dict] | None, str | None]":
    """Candidate projects for the cross-project kanban aggregate.

    This is scoped ENUMERATION, not authorization: it returns only projects the
    caller could already name for itself, and the per-project
    ``_authorize_task_actor`` gate applied by the aggregate route remains the
    authoritative check (defence in depth).  It must never surface a project the
    caller is not entitled to:

      * session owner/admin -> own projects (``list_for_user``) or all (admin);
      * registry-JWT agent   -> only projects it holds an active
        ``project_tasks`` grant for (grant-gated, taOS #1862).

    A caller with neither a session nor a Bearer never reaches this helper (the
    auth middleware 401s first), so the empty fallbacks below are belt-and-braces
    rather than the primary gate.

    Returns ``(projects, agent_grants, canonical_id)``: for a session
    owner/admin the last two are ``(None, None)``; for a registry-JWT agent
    ``agent_grants`` is the active ``project_tasks`` -> grant map resolved by a
    SINGLE ``check_agent_project_grants`` call (so the aggregate's per-project
    re-check is O(1) rather than an O(K) grant-store N+1), and ``canonical_id``
    is the verified agent identity.
    """
    uid = getattr(request.state, "user_id", None)
    if uid:
        user = CurrentUser(
            user_id=uid, is_admin=bool(getattr(request.state, "is_admin", False))
        )
        if user.is_admin:
            return await pstore.list_projects(status="active"), None, None
        return await pstore.list_for_user(uid, status="active"), None, None

    # No session: a registry-JWT agent (the middleware only passes a Bearer
    # through on the allowlist and leaves user_id=None).  Resolve WHO the agent
    # is AND its active project_tasks grants in one grant-store read.
    canonical_id, grants_by_project = await check_agent_project_grants(
        request, "project_tasks"
    )
    if not canonical_id:
        return [], None, None
    projects: list[dict] = []
    for pid in grants_by_project:
        p = await pstore.get_project(pid)
        if p is not None:
            projects.append(p)
    return projects, grants_by_project, canonical_id


@router.get("/api/projects/tasks/aggregate")
async def list_tasks_aggregate(
    request: Request,
    status: str | None = None,
):
    """Cross-project aggregate of the caller's kanban boards (READ ONLY).

    Returns every project the caller is authorized to see -- its own boards for
    a session owner, or the boards it holds a ``project_tasks`` grant for as an
    agent -- each with that project's tasks.  The per-project
    ``_authorize_task_actor`` gate is applied to EVERY candidate, so a caller
    entitled to project A can never receive project B in the aggregate (the
    whole point of this endpoint).  ``status`` filters tasks to
    ``open`` / ``claimed`` / ``closed`` and is validated up front (400 on
    anything else), so a typo is surfaced rather than silently returning an
    empty board.
    """
    if status is not None and status not in _TASK_STATUS_ENUM:
        return JSONResponse(
            {"error": f"invalid status {status!r}; must be one of open|claimed|closed"},
            status_code=400,
        )
    pstore = request.app.state.project_store
    store = request.app.state.project_task_store
    candidates, agent_grants, agent_canonical_id = await _caller_project_candidates(
        request, pstore
    )
    items: list[dict] = []
    for project in candidates:
        # The agent's grant map (and canonical_id) is resolved ONCE upstream and
        # reused here so the per-project check is O(1); for a session caller
        # agent_grants is None and _authorize_task_actor takes its normal path.
        try:
            auth = await _authorize_task_actor(
                request,
                pstore,
                project["id"],
                scope="project_tasks",
                agent_grants=agent_grants,
                agent_canonical_id=agent_canonical_id,
            )
        except HTTPException:
            # Any per-project authorization failure (agent not active, token
            # superseded, grant revoked mid-iteration, ...) is swallowed: a
            # project the caller is not entitled to is simply omitted rather
            # than aborting the whole aggregate with an unhandled 403/500 after
            # some boards were already collected.
            continue
        if isinstance(auth, JSONResponse):
            # Not authorized for THIS project -> excluded from the aggregate, so
            # it cannot leak another project's board to the caller.
            continue
        # Re-check status inside the loop (TOCTOU defence): a project archived
        # between the candidate listing and this per-project check must NOT leak
        # into the response -- the aggregate contract is "active projects only".
        # Read the FRESH object returned by _authorize_task_actor (auth[2]) rather
        # than the stale `project` loop variable from the candidates snapshot,
        # which may still report "active" after the project was archived.
        fresh_project = auth[2]
        if (fresh_project or {}).get("status") != "active":
            continue
        tasks = await store.list_tasks(project_id=project["id"], status=status)
        items.append(
            {
                "project_id": project["id"],
                "name": project.get("name"),
                "slug": project.get("slug"),
                "tasks": tasks,
            }
        )
    return {"items": items}


@router.get("/api/projects/{project_id}/tasks/ready")
async def ready_tasks(
    project_id: str,
    request: Request,
    element_id: str | None = None,
    limit: int = 50,
):
    """List ready (open, unclaimed, unblocked) tasks for the project.

    ``limit`` is clamped to ``[1, 500]`` in the store: a floor of 1 stops
    ``?limit=0`` / negative inputs from being silently widened to unbounded
    or the default 50 (the LIMIT -1 SQLite trap, see taosmd #415), and the
    500 cap keeps the route from streaming an unreasonable window back to a
    caller.  The view honours the ``blocked-on:<id>`` label convention
    alongside ``task_relationships`` edges (defect tsk-wkah3z).
    """
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    return {"items": await store.list_ready_tasks(
        project_id=project_id, element_id=element_id, limit=limit
    )}


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
    strikes = getattr(request.app.state, "task_strikes", None)
    if strikes is not None:
        t["strike_count"] = await strikes.count_strikes(task_id)
        t["latest_strike"] = await strikes.latest(task_id)
    return t


# Fields an agent holding project_tasks_update may PATCH. Everything else in
# UpdateTaskIn (assignee_id, parent_task_id, element_id, status, and any
# future field) is rejected 403 for agents so the surface stays minimal and
# future task fields are protected by default. Session owner/admin is unaffected.
_AGENT_EDITABLE_FIELDS = frozenset({"title", "body", "labels", "priority"})


@router.patch("/api/projects/{project_id}/tasks/{task_id}")
async def update_task(
    project_id: str,
    task_id: str,
    payload: UpdateTaskIn,
    request: Request,
):
    # Dual-auth: session owner/admin OR an agent holding project_tasks_update
    # bound to THIS project. The agent gate (authorship/lead) and field
    # whitelist are enforced below.
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(
        request, pstore, project_id, scope="project_tasks_update"
    )
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, project = auth
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Agent gate: an agent may PATCH only its OWN cards or cards on a project it
    # leads. A non-author non-lead agent is refused 403 (it has the scope, so the
    # project is not hidden; the refusal is an authorization failure, not a
    # scope mismatch).
    if is_agent:
        lead_id = project.get("lead_member_id")
        if existing.get("created_by") != actor_id and lead_id != actor_id:
            return JSONResponse(
                {"error": "agent may only edit its own cards or those it leads"},
                status_code=403,
            )

    # Field whitelist for agents: title, body, labels, priority ONLY.
    # Any other field that is set is rejected 403 (future fields included)
    # so the surface stays minimal and future task fields are protected by
    # default. assignee_id and parent_task_id stay human-only: an agent may
    # not reassign work to itself or rewire hierarchies.
    if is_agent:
        for f in payload.model_fields:
            if f not in _AGENT_EDITABLE_FIELDS and getattr(payload, f) is not None:
                return JSONResponse(
                    {"error": f"field {f!r} is not editable by agents"},
                    status_code=403,
                )

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

    estore = request.app.state.project_element_store
    update_fields: dict = {}
    for f in ("title", "body", "priority", "labels", "status", "assignee_id", "parent_task_id"):
        v = getattr(payload, f)
        if v is not None:
            update_fields[f] = v
    if payload.element_id is not None:
        if payload.element_id == "none":
            update_fields["element_id"] = _ELEMENT_CLEAR
        else:
            el_check = await _require_active_element(estore, project_id, payload.element_id)
            if isinstance(el_check, JSONResponse):
                return el_check
            update_fields["element_id"] = payload.element_id

    await store.update_task(task_id, **update_fields)
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


class MarkClaimableIn(_TaskRequestModelMixin, BaseModel):
    claimable: bool


@router.post("/api/projects/{project_id}/tasks/{task_id}/claimable")
async def mark_task_claimable(
    project_id: str,
    task_id: str,
    payload: MarkClaimableIn,
    request: Request,
):
    """Add or remove the ``claimable`` label on a task (fleet-pickup flag).

    LEAD-only curation: a project LEAD (session owner/admin, or the lead agent's
    ``project_tasks`` token) flags which cards the build fleet may pick up. This
    is deliberately narrower than PATCH ``update_task``: it toggles ONLY the
    ``claimable`` label and preserves every other label, so granting it to the
    lead agent does not widen the ``project_tasks`` scope beyond a single-label
    toggle.
    """
    pstore = request.app.state.project_store
    auth = await _authorize_project_lead(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    labels = list(existing.get("labels") or [])
    has = "claimable" in labels
    if payload.claimable and not has:
        labels.append("claimable")
    elif not payload.claimable and has:
        labels = [lbl for lbl in labels if lbl != "claimable"]
    else:
        return await store.get_task(task_id)  # already in the requested state
    await store.update_task(task_id, labels=labels)
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(
        project_id, actor_id, "task.claimable", {"task_id": task_id, "claimable": payload.claimable}
    )
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
    # Ownership-guard bypass: a card claimed by one agent may still be closed
    # by the project lead (lead_member_id), the project owner (user_id), or a
    # session admin.  The lead is typically an AGENT registry id, so an
    # admin/owner session caller (a USER id) can never equal it -- widen the
    # bypass to cover all three (issue #2191).
    force = (
        project.get("lead_member_id") == actor_id
        or project.get("user_id") == actor_id
        or bool(getattr(request.state, "is_admin", False))
    )
    ok = await store.close_task(task_id, closed_by=closed_by, reason=payload.reason, force=force)
    if not ok:
        if existing.get("claimed_by") and existing["claimed_by"] != closed_by:
            return JSONResponse({"error": "not claimed by you"}, status_code=409)
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


@router.post("/api/projects/{project_id}/tasks/{task_id}/unquarantine")
async def unquarantine_task(
    project_id: str,
    task_id: str,
    request: Request,
):
    """Return a quarantined card to the open pool and clear its strikes.

    LEAD-only curation, mirroring mark_task_claimable: only a project LEAD
    (session owner/admin, or the lead agent's ``project_tasks`` token) may
    retry a quarantined card -- a plain project_tasks worker cannot self
    un-quarantine. See ProjectTaskStore.unquarantine_task for the strike-clear
    behaviour.
    """
    pstore = request.app.state.project_store
    auth = await _authorize_project_lead(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.unquarantine_task(task_id, actor_id)
    if not ok:
        return JSONResponse({"error": "task is not quarantined"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, actor_id, "task.unquarantined", {"task_id": task_id})
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        await notifs.emit_event(
            "task.unquarantined", "Task unquarantined", f"{task_id} unquarantined by {actor_id}"
        )
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


class AddCommentIn(_TaskRequestModelMixin, BaseModel):
    body: str
    # Optional: an agent caller may omit it and the route pins it to the token
    # canonical_id. A session caller must still supply it (route enforces).
    author_id: str | None = None
    replies_to_comment_id: str | None = None


class CreateChecklistItemIn(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# Checklist routes
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project_id}/tasks/{task_id}/checklist-items")
async def create_checklist_item(
    project_id: str,
    task_id: str,
    payload: CreateChecklistItemIn,
    request: Request,
):
    """Create a checklist item for a task.

    Authorized as session owner/admin or an agent holding
    ``project_tasks_create`` on this project.
    """
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(
        request, pstore, project_id, scope="project_tasks_create"
    )
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_task_store
    t = await _require_task_in_project(store, project_id, task_id)
    if isinstance(t, JSONResponse):
        return t
    item = await store.create_checklist_item(
        task_id=task_id,
        text=payload.text,
        created_by=actor_id,
    )
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(
        project_id, actor_id, "checklist.item.created", {"task_id": task_id, "item_id": item["id"], "text": item["text"]}
    )
    return item


@router.get("/api/projects/{project_id}/tasks/{task_id}/checklist-items")
async def list_checklist_items(
    project_id: str,
    task_id: str,
    request: Request,
    include_archived: bool = False,
):
    """List checklist items for a task.

    By default shows only non-archived items. Set ``include_archived=true``
    to see all items including archived ones.
    """
    pstore = request.app.state.project_store
    auth = await _authorize_task_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    items = await store.list_checklist_items(
        task_id=task_id,
        include_archived=include_archived,
    )
    return {"items": items}


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


# ---------------------------------------------------------------------------
# Element routes (slice 1 of docs/design/projects-nested-elements.md).
# Owner-gated exactly like the member routes: session owner/admin via
# _get_owned_project, with existence-hiding 404. External agent tokens get no
# new mutation surface in v1.
# ---------------------------------------------------------------------------

class CreateElementIn(BaseModel):
    name: str
    slug: str | None = None
    type: str = "generic"
    description: str = ""
    assignee_id: str | None = None
    settings: dict = Field(default_factory=dict)


class UpdateElementIn(BaseModel):
    name: str | None = None
    type: str | None = None
    description: str | None = None
    assignee_id: str | None = None
    settings: dict | None = None


def _ensure_element_folder(request: Request, project: dict, element: dict) -> None:
    """Best-effort files subfolder for the element. The DB row is authoritative;
    a disk failure must never block the element creation. If a folder with the
    element's slug already exists (a user-made folder), it is adopted rather
    than overwritten, matching the design doc's adopt-existing semantics."""
    try:
        ensure_element_folder(
            request.app.state.projects_root, project["slug"], element["slug"]
        )
    except Exception as exc:
        logger.warning(
            "element files folder create failed for %s/%s: %s",
            project.get("slug"), element.get("slug"), exc,
        )


async def _validate_element_assignee(pstore, project_id: str, assignee_id: "str | None") -> "bool":
    """An element assignee must name a current project member, or be None."""
    if assignee_id is None:
        return True
    members = await pstore.list_members(project_id)
    return any(m["member_id"] == assignee_id for m in members)


@router.post("/api/projects/{project_id}/elements")
async def create_element(
    project_id: str,
    payload: CreateElementIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    project = await _get_owned_project(pstore, project_id, user)
    if isinstance(project, JSONResponse):
        return project
    if not await _validate_element_assignee(pstore, project_id, payload.assignee_id):
        return JSONResponse({"error": "assignee is not a project member"}, status_code=400)
    try:
        el = await estore.create_element(
            project_id=project_id,
            name=payload.name,
            slug=payload.slug,
            type=payload.type,
            description=payload.description,
            assignee_id=payload.assignee_id,
            settings=payload.settings,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    _ensure_element_folder(request, project, el)
    await pstore.log_activity(
        project_id, user.user_id, "element.created",
        {"element_id": el["id"], "slug": el["slug"], "type": el["type"]},
    )
    return el


@router.get("/api/projects/{project_id}/elements")
async def list_elements(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    project = await _get_owned_project(pstore, project_id, user)
    if isinstance(project, JSONResponse):
        return project
    return {"items": await estore.list_elements(project_id)}


@router.get("/api/projects/{project_id}/elements/{element_id}")
async def get_element(
    project_id: str,
    element_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    project = await _get_owned_project(pstore, project_id, user)
    if isinstance(project, JSONResponse):
        return project
    el = await estore.get_element(element_id)
    if el is None or el["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return el


@router.patch("/api/projects/{project_id}/elements/{element_id}")
async def update_element(
    project_id: str,
    element_id: str,
    payload: UpdateElementIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    project = await _get_owned_project(pstore, project_id, user)
    if isinstance(project, JSONResponse):
        return project
    el = await estore.get_element(element_id)
    if el is None or el["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    if payload.assignee_id is not None and not await _validate_element_assignee(
        pstore, project_id, payload.assignee_id
    ):
        return JSONResponse({"error": "assignee is not a project member"}, status_code=400)
    updated = await estore.update_element(
        element_id,
        name=payload.name,
        type=payload.type,
        description=payload.description,
        assignee_id=payload.assignee_id,
        settings=payload.settings,
    )
    await pstore.log_activity(
        project_id, user.user_id, "element.updated", {"element_id": element_id}
    )
    return updated


@router.post("/api/projects/{project_id}/elements/{element_id}/archive")
async def archive_element(
    project_id: str,
    element_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    project = await _get_owned_project(pstore, project_id, user)
    if isinstance(project, JSONResponse):
        return project
    el = await estore.get_element(element_id)
    if el is None or el["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    await estore.archive_element(element_id)
    await pstore.log_activity(
        project_id, user.user_id, "element.archived", {"element_id": element_id}
    )
    return await estore.get_element(element_id)


@router.delete("/api/projects/{project_id}/elements/{element_id}")
async def delete_element(
    project_id: str,
    element_id: str,
    request: Request,
    mode: str = "strict",
    user: CurrentUser = Depends(current_user),
):
    pstore = request.app.state.project_store
    estore = request.app.state.project_element_store
    project = await _get_owned_project(pstore, project_id, user)
    if isinstance(project, JSONResponse):
        return project
    el = await estore.get_element(element_id)
    if el is None or el["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    counts = await estore.count_element_items(project_id, element_id)
    if (counts["total_tasks"] > 0 or counts["canvas_items"] > 0) and mode != "untag":
        return JSONResponse(
            {
                "error": "element has tagged items",
                "open_tasks": counts["open_tasks"],
                "total_tasks": counts["total_tasks"],
                "canvas_items": counts["canvas_items"],
            },
            status_code=409,
        )
    await estore.delete_element(element_id, untag=(mode == "untag"))
    await pstore.log_activity(
        project_id, user.user_id, "element.deleted",
        {"element_id": element_id, "mode": mode},
    )
    return {"ok": True}

