from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import current_user
from tinyagentos.routes.auth import _require_admin

router = APIRouter()


class CreateSubmissionBody(BaseModel):
    artifact_id: str
    artifact_kind: str
    title: str
    publish_mode: str


class RejectBody(BaseModel):
    reason: str


@router.post("/api/store/submissions")
async def create_submission(request: Request, body: CreateSubmissionBody):
    user = current_user(request)
    store = request.app.state.store_submissions
    try:
        row = await store.create(
            artifact_id=body.artifact_id,
            artifact_kind=body.artifact_kind,
            owner_id=user.user_id,
            title=body.title,
            publish_mode=body.publish_mode,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return row


@router.post("/api/store/submissions/{submission_id}/submit")
async def submit_submission(request: Request, submission_id: str):
    user = current_user(request)
    store = request.app.state.store_submissions
    row = await store.get(submission_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if row["owner_id"] != user.user_id and not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        row = await store.submit(submission_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return row


@router.post("/api/store/submissions/{submission_id}/approve")
async def approve_submission(request: Request, submission_id: str):
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = request.app.state.store_submissions
    row = await store.get(submission_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        row = await store.approve(submission_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return row


@router.post("/api/store/submissions/{submission_id}/reject")
async def reject_submission(request: Request, submission_id: str, body: RejectBody):
    ok, err = _require_admin(request)
    if ok is False:
        return err
    store = request.app.state.store_submissions
    row = await store.get(submission_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        row = await store.reject(submission_id, body.reason)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return row


@router.get("/api/store/submissions/mine")
async def list_mine(request: Request):
    user = current_user(request)
    store = request.app.state.store_submissions
    return await store.list(owner_id=user.user_id)


@router.get("/api/store/submissions")
async def list_submissions(request: Request, status: str | None = None):
    ok, err = _require_admin(request)
    if not ok:
        return err
    store = request.app.state.store_submissions
    return await store.list(status=status)


@router.get("/api/store/submissions/{submission_id}")
async def get_submission(request: Request, submission_id: str):
    user = current_user(request)
    store = request.app.state.store_submissions
    row = await store.get(submission_id)
    if row is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Only the owner or an admin may read a non-published submission (IDOR guard).
    if row["status"] != "published" and row["owner_id"] != user.user_id and not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return row
