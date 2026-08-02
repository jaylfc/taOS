from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.agent_token_auth import (
    PROJECT_SCOPE_MISMATCH_DETAIL,
    check_agent_scope_for_project,
)
from tinyagentos.auth_context import CurrentUser
from tinyagentos.routes.projects import _get_owned_project

router = APIRouter()


class DocReviewUpdate(BaseModel):
    state: str = Field(..., description="Target review state")


async def _authorize_doc_review_actor(
    request: Request, pstore, project_id: str
) -> "tuple[str, bool, dict] | JSONResponse":
    uid = getattr(request.state, "user_id", None)
    if uid:
        user = CurrentUser(
            user_id=uid,
            is_admin=bool(getattr(request.state, "is_admin", False)),
        )
        project_or_err = await _get_owned_project(pstore, project_id, user)
        if isinstance(project_or_err, JSONResponse):
            return project_or_err
        return (user.user_id, False, project_or_err)

    try:
        caller = await check_agent_scope_for_project(
            request, "project_doc_review", project_id
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
    return (caller, True, project)


@router.put("/api/projects/{project_id}/doc-review/{doc_path:path}")
async def update_doc_review(
    project_id: str,
    doc_path: str,
    payload: DocReviewUpdate,
    request: Request,
):
    store = request.app.state.doc_review_store
    pstore = request.app.state.project_store

    auth = await _authorize_doc_review_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth
    actor_id, _is_agent, _project = auth

    try:
        review = await store.set_review_state(
            project_id, doc_path, payload.state, actor_id
        )
    except ValueError as exc:
        detail = str(exc)
        if "invalid transition" in detail:
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)

    return review


@router.get("/api/projects/{project_id}/doc-review/{doc_path:path}")
async def get_doc_review(
    project_id: str,
    doc_path: str,
    request: Request,
):
    store = request.app.state.doc_review_store
    pstore = request.app.state.project_store

    auth = await _authorize_doc_review_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth

    review = await store.get_review(project_id, doc_path)
    if review is None:
        return {"project_id": project_id, "doc_path": doc_path, "review_state": None}
    return review


@router.get("/api/projects/{project_id}/doc-reviews")
async def list_doc_reviews(
    project_id: str,
    request: Request,
    state: str | None = None,
):
    store = request.app.state.doc_review_store
    pstore = request.app.state.project_store

    auth = await _authorize_doc_review_actor(request, pstore, project_id)
    if isinstance(auth, JSONResponse):
        return auth

    reviews = await store.list_reviews(project_id, state=state)
    return {"items": reviews}
