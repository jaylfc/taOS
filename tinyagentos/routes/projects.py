from __future__ import annotations

import logging
import re
import time as _time

import asyncio as _asyncio
import json as _json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

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


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict) and "id" in user:
        return user["id"]
    return "system"


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
async def create_project(payload: CreateProjectIn, request: Request):
    store = request.app.state.project_store
    try:
        p = await store.create_project(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            settings=payload.settings,
            created_by=_user_id(request),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    await store.log_activity(p["id"], _user_id(request), "project.created", {"slug": p["slug"]})
    _mirror(request, p)
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel
        await ensure_a2a_channel(
            request.app.state.chat_channels,
            request.app.state.project_store,
            p["id"],
        )
    except Exception:
        logger.warning("a2a ensure failed for new project %s", p.get("id"), exc_info=True)
    return p


@router.get("/api/projects")
async def list_projects(request: Request, status: str | None = "active"):
    store = request.app.state.project_store
    items = await store.list_projects(status=status)
    return {"items": items}


@router.get("/api/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    store = request.app.state.project_store
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return p


@router.patch("/api/projects/{project_id}")
async def update_project(project_id: str, payload: UpdateProjectIn, request: Request):
    store = request.app.state.project_store
    await store.update_project(
        project_id,
        name=payload.name,
        description=payload.description,
        settings=payload.settings,
    )
    p = await store.get_project(project_id)
    if p is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.log_activity(project_id, _user_id(request), "project.updated", payload.model_dump(exclude_none=True))
    _mirror(request, p)
    return p


@router.post("/api/projects/{project_id}/archive")
async def archive_project(project_id: str, request: Request):
    store = request.app.state.project_store
    if await store.get_project(project_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.set_status(project_id, "archived")
    p = await store.get_project(project_id)
    await store.log_activity(project_id, _user_id(request), "project.archived", {})
    return p


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    store = request.app.state.project_store
    project = await store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    await store.set_status(project_id, "deleted")
    await store.log_activity(project_id, _user_id(request), "project.deleted", {})

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
async def add_member(project_id: str, payload: AddMemberIn, request: Request):
    store = request.app.state.project_store
    project = await store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)

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
        project_id, _user_id(request), "member.added",
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
        )
    except Exception:
        logger.warning("a2a ensure failed for project %s on add_member", project_id, exc_info=True)
    return next(m for m in members if m["member_id"] == member_id)


@router.get("/api/projects/{project_id}/members")
async def list_members(project_id: str, request: Request):
    store = request.app.state.project_store
    return {"items": await store.list_members(project_id)}


@router.delete("/api/projects/{project_id}/members/{member_id}")
async def remove_member(project_id: str, member_id: str, request: Request):
    store = request.app.state.project_store
    await store.remove_member(project_id, member_id)
    await store.log_activity(project_id, _user_id(request), "member.removed", {"member_id": member_id})
    project = await store.get_project(project_id)
    members = await store.list_members(project_id)
    if project is not None:
        _mirror(request, {**project, "members": members})
    try:
        from tinyagentos.projects.a2a import ensure_a2a_channel
        await ensure_a2a_channel(
            request.app.state.chat_channels,
            request.app.state.project_store,
            project_id,
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


class AddRelIn(BaseModel):
    to_task_id: str
    kind: str


# ---------------------------------------------------------------------------
# Task routes — order matters: /tasks/ready before /tasks/{task_id}
# ---------------------------------------------------------------------------

@router.post("/api/projects/{project_id}/tasks")
async def create_task(project_id: str, payload: CreateTaskIn, request: Request):
    store = request.app.state.project_task_store
    pstore = request.app.state.project_store
    if await pstore.get_project(project_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
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
        created_by=_user_id(request),
    )
    _beads_mark_dirty(request, project_id)
    await pstore.log_activity(project_id, _user_id(request), "task.created", {"task_id": t["id"], "title": t["title"]})
    return t


@router.get("/api/projects/{project_id}/tasks")
async def list_tasks(project_id: str, request: Request, status: str | None = None):
    store = request.app.state.project_task_store
    return {"items": await store.list_tasks(project_id=project_id, status=status)}


@router.get("/api/projects/{project_id}/tasks/ready")
async def ready_tasks(project_id: str, request: Request):
    store = request.app.state.project_task_store
    return {"items": await store.list_ready_tasks(project_id=project_id)}


@router.get("/api/projects/{project_id}/tasks/{task_id}")
async def get_task(project_id: str, task_id: str, request: Request):
    store = request.app.state.project_task_store
    t = await store.get_task(task_id)
    if t is None or t["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return t


@router.patch("/api/projects/{project_id}/tasks/{task_id}")
async def update_task(project_id: str, task_id: str, payload: UpdateTaskIn, request: Request):
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
async def claim_task(project_id: str, task_id: str, payload: ClaimIn, request: Request):
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.claim_task(task_id, payload.claimer_id)
    if not ok:
        return JSONResponse({"error": "already claimed"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    pstore = request.app.state.project_store
    await pstore.log_activity(project_id, payload.claimer_id, "task.claimed", {"task_id": task_id})
    return await store.get_task(task_id)


@router.post("/api/projects/{project_id}/tasks/{task_id}/release")
async def release_task(project_id: str, task_id: str, payload: ReleaseIn, request: Request):
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.release_task(task_id, payload.releaser_id)
    if not ok:
        return JSONResponse({"error": "not claimed by releaser"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    return await store.get_task(task_id)


@router.post("/api/projects/{project_id}/tasks/{task_id}/close")
async def close_task(project_id: str, task_id: str, payload: CloseIn, request: Request):
    store = request.app.state.project_task_store
    existing = await store.get_task(task_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    ok = await store.close_task(task_id, closed_by=payload.closed_by, reason=payload.reason)
    if not ok:
        return JSONResponse({"error": "cannot close"}, status_code=409)
    _beads_mark_dirty(request, project_id)
    pstore = request.app.state.project_store
    await pstore.log_activity(project_id, payload.closed_by, "task.closed", {"task_id": task_id})
    project = await pstore.get_project(project_id)
    task = await store.get_task(task_id)
    qmd = getattr(request.app.state, "qmd_client", None)
    if qmd is not None and project is not None and task is not None:
        try:
            from tinyagentos.projects.lifecycle import index_closed_task
            await index_closed_task(qmd, project, task)
        except Exception:
            await pstore.log_activity(
                project_id, payload.closed_by, "task.qmd_index_failed", {"task_id": task_id}
            )
    return task


async def _require_task_in_project(
    store, project_id: str, task_id: str
) -> dict | JSONResponse:
    task = await store.get_task(task_id)
    if task is None or task["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return task


@router.post("/api/projects/{project_id}/tasks/{task_id}/relationships")
async def add_relationship(project_id: str, task_id: str, payload: AddRelIn, request: Request):
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
            created_by=_user_id(request),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _beads_mark_dirty(request, project_id)
    return rel


class AddCommentIn(BaseModel):
    body: str
    author_id: str
    replies_to_comment_id: str | None = None


@router.post("/api/projects/{project_id}/tasks/{task_id}/comments")
async def add_comment(project_id: str, task_id: str, payload: AddCommentIn, request: Request):
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    try:
        return await store.add_comment(
            task_id=task_id,
            author_id=payload.author_id,
            body=payload.body,
            replies_to_comment_id=payload.replies_to_comment_id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/api/projects/{project_id}/tasks/{task_id}/comments")
async def list_comments(project_id: str, task_id: str, request: Request):
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    return {"items": await store.list_comments(task_id)}


@router.get("/api/projects/{project_id}/tasks/{task_id}/relationships")
async def list_relationships(project_id: str, task_id: str, request: Request, direction: str = "from"):
    store = request.app.state.project_task_store
    guard = await _require_task_in_project(store, project_id, task_id)
    if isinstance(guard, JSONResponse):
        return guard
    return {"items": await store.list_relationships(task_id, direction=direction)}


@router.get("/api/projects/{project_id}/activity")
async def activity_feed(project_id: str, request: Request, limit: int = 100):
    store = request.app.state.project_store
    return {"items": await store.list_activity(project_id, limit=limit)}


@router.get("/api/projects/{project_id}/memory/search")
async def memory_search(project_id: str, request: Request, q: str, limit: int = 10):
    store = request.app.state.project_store
    project = await store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
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
async def project_events(project_id: str, request: Request):
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
async def beads_export(project_id: str, request: Request):
    """Force a synchronous render of the project's .beads/tasks.jsonl
    snapshot. Returns 503 when the bridge isn't running."""
    bridge = getattr(request.app.state, "beads_bridge", None)
    if bridge is None:
        return JSONResponse({"error": "beads bridge not running"}, status_code=503)
    pstore = request.app.state.project_store
    if await pstore.get_project(project_id) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = await bridge.export_now(project_id)
    if path is None:
        return JSONResponse({"error": "export failed"}, status_code=500)
    return {"path": str(path)}
