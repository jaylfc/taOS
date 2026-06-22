"""Decisions API: the human-in-the-loop inbox.

An agent posts a decision (a choice it needs from the user); it queues until
answered. Consent and explicitly-blocking decisions raise a higher-priority
notification; everything else is a normal badge + notification.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.decisions.decision_store import DECISION_TYPES, PRIORITIES

router = APIRouter()


class OptionIn(BaseModel):
    label: str
    value: str | None = None
    recommended: bool = False
    rationale: str = ""


class DecisionIn(BaseModel):
    from_agent: str
    question: str
    type: str
    options: list[OptionIn] = []
    context: str = ""
    priority: str = "normal"
    project_id: str | None = None
    deadline: float | None = None
    parent_decision_id: str | None = None
    checkpoint_ref: str | None = None
    timeline_id: str | None = None


class AnswerIn(BaseModel):
    value: object
    answered_by: str = ""


@router.post("/api/decisions")
async def create_decision(body: DecisionIn, request: Request, user: CurrentUser = Depends(current_user)):
    if body.type not in DECISION_TYPES:
        return JSONResponse({"error": f"type must be one of {DECISION_TYPES}"}, status_code=400)
    if body.priority not in PRIORITIES:
        return JSONResponse({"error": f"priority must be one of {PRIORITIES}"}, status_code=400)
    if body.type in ("single_select", "multi_select") and not body.options:
        return JSONResponse({"error": "select types require options"}, status_code=400)

    store = request.app.state.decision_store
    decision = await store.create(
        from_agent=body.from_agent,
        question=body.question,
        type=body.type,
        options=[o.model_dump() for o in body.options],
        context=body.context,
        priority=body.priority,
        project_id=body.project_id,
        user_id=user.user_id,
        deadline=body.deadline,
        parent_decision_id=body.parent_decision_id,
        checkpoint_ref=body.checkpoint_ref,
        timeline_id=body.timeline_id,
    )

    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        # Best effort: a notification failure must not fail the queued decision.
        try:
            await notifs.add(
                title="Decision needed",
                message=f"{body.from_agent} needs a decision: {body.question[:120]}",
                level="warning" if body.priority == "blocking" else "info",
                source="decisions",
            )
        except Exception:
            pass
    return decision


@router.get("/api/decisions")
async def list_decisions(
    request: Request,
    status: str | None = None,
    project_id: str | None = None,
    limit: int = 200,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.decision_store
    # Non-admins see only their own decisions; admins see all.
    uid = None if user.is_admin else user.user_id
    items = await store.list(status=status, project_id=project_id, user_id=uid, limit=limit)
    return {"items": items}


@router.get("/api/decisions/{decision_id}")
async def get_decision(decision_id: str, request: Request, user: CurrentUser = Depends(current_user)):
    store = request.app.state.decision_store
    d = await store.get(decision_id)
    if d is None or (not user.is_admin and d["user_id"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@router.post("/api/decisions/{decision_id}/answer")
async def answer_decision(decision_id: str, body: AnswerIn, request: Request, user: CurrentUser = Depends(current_user)):
    store = request.app.state.decision_store
    existing = await store.get(decision_id)
    if existing is None or (not user.is_admin and existing["user_id"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)

    # For select types, the answer must reference the declared options so a
    # stale or malformed client cannot record an arbitrary value.
    dtype = existing.get("type")
    if dtype in ("single_select", "multi_select"):
        valid = {
            o.get("value")
            for o in (existing.get("options") or [])
            if o.get("value") is not None
        }
        if valid:
            if dtype == "single_select":
                if body.value not in valid:
                    return JSONResponse({"error": "answer is not one of the options"}, status_code=400)
            else:
                vals = body.value if isinstance(body.value, list) else None
                if vals is None or any(v not in valid for v in vals):
                    return JSONResponse({"error": "answer must be a subset of the options"}, status_code=400)

    answered_by = body.answered_by or user.user_id or "user"
    updated = await store.answer(decision_id, body.value, answered_by)
    if updated is None:
        return JSONResponse({"error": "already answered or not pending"}, status_code=409)
    return updated
