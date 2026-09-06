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

# Scope for project-scoped notes: a single scope covers read + write. Notes are
# a lightweight persistent surface (ideas, not lifecycle-critical cards), so a
# single project_notes grant lets an approved agent read and post/edit/delete
# notes on ITS project only. The route verifies the JWT + grant + project binding.
_NOTES_SCOPE = "project_notes"


class CreateNoteIn(BaseModel):
    title: str
    body: str = ""


class UpdateNoteIn(BaseModel):
    title: str | None = None
    body: str | None = None


async def _authorize_notes_actor(
    request: Request, pstore, project_id: str
) -> "tuple[str, bool, dict] | JSONResponse":
    """Resolve the actor for a project-notes route that accepts EITHER a session
    owner/admin OR an approved external agent's registry JWT holding _NOTES_SCOPE
    bound to THIS project.

    Mirrors ``_authorize_task_actor`` (routes/projects.py): session non-owners
    and wrong-project tokens both collapse into an existence-hiding 404 so a
    caller can never confirm another project's existence. The route takes
    ``request: Request`` and auths INSIDE the handler (not via Depends) because
    ``Depends(current_user)`` would 401 an agent with no session before it runs.

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
        caller = await check_agent_scope_for_project(request, _NOTES_SCOPE, project_id)
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


@router.get("/api/projects/{project_id}/notes")
async def list_notes(project_id: str, request: Request):
    pstore = request.app.state.project_store
    auth = await _authorize_notes_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    store = request.app.state.project_notes_store
    return {"items": await store.list_notes(project_id)}


@router.post("/api/projects/{project_id}/notes")
async def create_note(
    project_id: str,
    payload: CreateNoteIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_notes_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, is_agent, _project = auth
    author_kind = "agent" if is_agent else "user"
    store = request.app.state.project_notes_store
    title = payload.title or ""
    note = await store.create_note(
        project_id=project_id,
        title=title,
        body=payload.body,
        author_id=actor_id,
        author_kind=author_kind,
    )
    await pstore.log_activity(
        project_id, actor_id, "note.created", {"note_id": note["id"], "title": note["title"]}
    )
    return note


@router.patch("/api/projects/{project_id}/notes/{note_id}")
async def update_note(
    project_id: str,
    note_id: str,
    payload: UpdateNoteIn,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_notes_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_notes_store
    existing = await store.get_note(note_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    updated = await store.update_note(
        note_id,
        title=payload.title,
        body=payload.body,
    )
    await pstore.log_activity(
        project_id, actor_id, "note.updated", {"note_id": note_id}
    )
    return updated


@router.delete("/api/projects/{project_id}/notes/{note_id}")
async def delete_note(
    project_id: str,
    note_id: str,
    request: Request,
):
    pstore = request.app.state.project_store
    auth = await _authorize_notes_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth
    store = request.app.state.project_notes_store
    existing = await store.get_note(note_id)
    if existing is None or existing["project_id"] != project_id:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.delete_note(note_id)
    await pstore.log_activity(
        project_id, actor_id, "note.deleted", {"note_id": note_id}
    )
    return {"ok": True}
