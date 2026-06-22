"""Decisions API: the human-in-the-loop inbox.

An agent posts a decision (a choice it needs from the user); it queues until
answered. Consent and explicitly-blocking decisions raise a higher-priority
notification; everything else is a normal badge + notification.
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.decisions.decision_store import DECISION_TYPES, PRIORITIES

router = APIRouter()

# Answer-routing: when a decision is answered, post the answer back to the
# asking agent on the A2A bus so off-session agents (taOSmd-dev, owl lanes,
# deployed agents) can pick it up. @taOS-dev polls the API instead.
_DEFAULT_BUS_URL = "http://127.0.0.1:7900"
_ANSWER_THREAD = "decisions"


def _bus_url() -> str:
    return os.environ.get("TAOS_A2A_BUS_URL", _DEFAULT_BUS_URL).rstrip("/")


def _answer_text(decision: dict, value) -> str:
    """Render a select answer using its option labels; pass others through."""
    opts = {o.get("value"): o.get("label") for o in (decision.get("options") or [])}
    vals = value if isinstance(value, list) else [value]
    return ", ".join(str(opts.get(v, v)) for v in vals)


async def _route_answer_to_agent(decision: dict, value) -> None:
    """Best-effort: post the recorded answer back to the asking agent on the
    A2A bus. Never raises; the answer is already persisted and the agent can
    also poll GET /api/decisions/{id}."""
    agent = (decision.get("from_agent") or "").strip()
    if not agent.startswith("@"):
        return
    body = (
        f"{agent} decision {decision.get('id')} answered: "
        f"{decision.get('question', '')} -> {_answer_text(decision, value)}"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{_bus_url()}/a2a/send",
                json={"from": "@taOS-decisions", "thread": _ANSWER_THREAD, "body": body},
            )
    except Exception:
        # Delivery is best-effort; do not fail the answer on a bus hiccup.
        pass


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

    # L1 revisit/supersede: a decision may replace an earlier one going forward.
    # Validate the parent belongs to this user before we create + supersede it.
    parent_id = body.parent_decision_id
    if parent_id:
        parent = await store.get(parent_id)
        if parent is None or (not user.is_admin and parent["user_id"] != user.user_id):
            return JSONResponse({"error": "parent_decision_id not found"}, status_code=400)

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
        parent_decision_id=parent_id,
        checkpoint_ref=body.checkpoint_ref,
        timeline_id=body.timeline_id,
    )

    # Mark the parent superseded only after the replacement is persisted.
    if parent_id:
        await store.supersede(parent_id)

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


@router.get("/api/decisions/{decision_id}/history")
async def decision_history(decision_id: str, request: Request, user: CurrentUser = Depends(current_user)):
    """The supersession lineage for a decision, oldest first: walk the
    parent_decision_id chain (L1). Cycle-guarded."""
    store = request.app.state.decision_store
    chain: list[dict] = []
    seen: set[str] = set()
    cur_id: str | None = decision_id
    while cur_id and cur_id not in seen:
        seen.add(cur_id)
        d = await store.get(cur_id)
        if d is None:
            break
        if not user.is_admin and d["user_id"] != user.user_id:
            return JSONResponse({"error": "not found"}, status_code=404)
        chain.append(d)
        cur_id = d.get("parent_decision_id")
    if not chain:
        return JSONResponse({"error": "not found"}, status_code=404)
    chain.reverse()
    return {"items": chain}


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
    await _route_answer_to_agent(updated, body.value)
    return updated
