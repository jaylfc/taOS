from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_token_auth import (
    PROJECT_SCOPE_MISMATCH_DETAIL,
    check_agent_scope_for_project,
)
from tinyagentos.auth_context import CurrentUser
from tinyagentos.routes.projects import _get_owned_project

router = APIRouter()

_LISTS_SCOPE = "project_lists"


class CreateListIn(BaseModel):
    title: str
    description: str = ""


class UpdateListIn(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class AddEntryIn(BaseModel):
    text: str
    category: str | None = None
    position: int | None = None


class UpdateEntryIn(BaseModel):
    text: str | None = None
    category: str | None = None
    status: str | None = None
    done: bool | None = None
    position: int | None = None


class ReorderEntryIn(BaseModel):
    id: str
    position: int


class ReorderIn(BaseModel):
    entries: list[ReorderEntryIn]


async def _authorize_lists_actor(
    request: Request, pstore, project_id: str
) -> "tuple[str, bool, dict] | JSONResponse":
    """Resolve the actor for a project-lists route that accepts EITHER a session
    owner/admin OR an approved external agent's registry JWT holding _LISTS_SCOPE
    bound to THIS project.

    Mirrors ``_authorize_notes_actor`` (routes/project_notes.py): session
    non-owners and wrong-project tokens both collapse into an existence-hiding
    404 so a caller can never confirm another project's existence. The route
    takes ``request: Request`` and auths INSIDE the handler (not via Depends)
    because ``Depends(current_user)`` would 401 an agent with no session before
    it runs.

    Returns ``(actor_id, is_agent, project)`` on success, or a JSONResponse to
    return directly.
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
        caller = await check_agent_scope_for_project(request, _LISTS_SCOPE, project_id)
    except HTTPException as exc:
        if exc.status_code == 403 and exc.detail == PROJECT_SCOPE_MISMATCH_DETAIL:
            return JSONResponse({"error": "not found"}, status_code=404)
        raise
    if caller is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    project = await pstore.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return (caller, True, project)


@router.post("/api/projects/{project_id}/lists")
async def create_list(
    project_id: str,
    payload: CreateListIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_lists_store
    lst = await store.create_list(
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        created_by=actor_id,
    )
    await pstore.log_activity(
        project_id, actor_id, "list.created", {"list_id": lst["id"], "title": lst["title"]}
    )
    return lst


@router.get("/api/projects/{project_id}/lists")
async def list_lists(project_id: str, request: Request):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_lists_store
    return {"items": await store.list_lists(project_id)}


@router.get("/api/projects/{project_id}/lists/{list_id}")
async def get_list(project_id: str, list_id: str, request: Request):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_lists_store
    lst = await store.get_list(list_id)
    if lst is None or lst["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    return lst


@router.patch("/api/projects/{project_id}/lists/{list_id}")
async def update_list(
    project_id: str,
    list_id: str,
    payload: UpdateListIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_lists_store
    existing = await store.get_list(list_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    updated = await store.update_list(
        list_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
    )
    await pstore.log_activity(
        project_id, actor_id, "list.updated", {"list_id": list_id}
    )
    return updated


@router.delete("/api/projects/{project_id}/lists/{list_id}")
async def delete_list(project_id: str, list_id: str, request: Request):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_lists_store
    existing = await store.get_list(list_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.delete_list(list_id)
    await pstore.log_activity(
        project_id, actor_id, "list.deleted", {"list_id": list_id}
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/lists/{list_id}/entries")
async def add_entry(
    project_id: str,
    list_id: str,
    payload: AddEntryIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, _project = auth
    store = request.app.state.project_list_entries_store
    lst_store = request.app.state.project_lists_store
    lst = await lst_store.get_list(list_id)
    if lst is None or lst["project_id"] != project_id:
        return JSONResponse({"error": "list not found"}, status_code=404)
    author_kind = "agent" if is_agent else "user"
    entry = await store.add_entry(
        list_id=list_id,
        project_id=project_id,
        text=payload.text,
        original_text=payload.text,
        category=payload.category,
        author_kind=author_kind,
        author_id=actor_id,
        position=payload.position,
    )
    await pstore.log_activity(
        project_id, actor_id, "entry.created", {"entry_id": entry["id"], "list_id": list_id}
    )
    return entry


@router.get("/api/projects/{project_id}/lists/{list_id}/entries")
async def list_entries(project_id: str, list_id: str, request: Request):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_list_entries_store
    lst_store = request.app.state.project_lists_store
    lst = await lst_store.get_list(list_id)
    if lst is None or lst["project_id"] != project_id:
        return JSONResponse({"error": "list not found"}, status_code=404)
    return {"items": await store.list_entries(project_id=project_id, list_id=list_id)}


@router.patch("/api/projects/{project_id}/lists/{list_id}/entries/{entry_id}")
async def update_entry(
    project_id: str,
    list_id: str,
    entry_id: str,
    payload: UpdateEntryIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_list_entries_store
    lst_store = request.app.state.project_lists_store
    lst = await lst_store.get_list(list_id)
    if lst is None or lst["project_id"] != project_id:
        return JSONResponse({"error": "list not found"}, status_code=404)
    existing = await store.get_entry(entry_id)
    if existing is None or existing["project_id"] != project_id or existing["list_id"] != list_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    updated = await store.update_entry(
        entry_id,
        text=payload.text,
        category=payload.category,
        status=payload.status,
        done=1 if payload.done else (0 if payload.done is not None else None),
        position=payload.position,
        edited_by=actor_id,
    )
    await pstore.log_activity(
        project_id, actor_id, "entry.updated", {"entry_id": entry_id, "list_id": list_id}
    )
    return updated


@router.delete("/api/projects/{project_id}/lists/{list_id}/entries/{entry_id}")
async def delete_entry(project_id: str, list_id: str, entry_id: str, request: Request):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_list_entries_store
    lst_store = request.app.state.project_lists_store
    lst = await lst_store.get_list(list_id)
    if lst is None or lst["project_id"] != project_id:
        return JSONResponse({"error": "list not found"}, status_code=404)
    existing = await store.get_entry(entry_id)
    if existing is None or existing["project_id"] != project_id or existing["list_id"] != list_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.delete_entry(entry_id)
    await pstore.log_activity(
        project_id, actor_id, "entry.deleted", {"entry_id": entry_id, "list_id": list_id}
    )
    return {"ok": True}


@router.post("/api/projects/{project_id}/lists/{list_id}/entries/reorder")
async def reorder_entries(
    project_id: str,
    list_id: str,
    payload: ReorderIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_lists_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_list_entries_store
    lst_store = request.app.state.project_lists_store
    lst = await lst_store.get_list(list_id)
    if lst is None or lst["project_id"] != project_id:
        return JSONResponse({"error": "list not found"}, status_code=404)
    ok = await store.reorder_entries(
        project_id, list_id, [e.model_dump() for e in payload.entries]
    )
    if not ok:
        return JSONResponse({"error": "reorder failed"}, status_code=400)
    await pstore.log_activity(
        project_id, actor_id, "entry.reordered", {"list_id": list_id}
    )
    return {"ok": True}
