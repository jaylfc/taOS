"""Decisions API: the human-in-the-loop inbox.

An agent posts a decision (a choice it needs from the user); it queues until
answered. Consent and explicitly-blocking decisions raise a higher-priority
notification; everything else is a normal badge + notification.
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.decisions.decision_store import DECISION_TYPES, PRIORITIES

logger = logging.getLogger(__name__)

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

    @model_validator(mode="after")
    def _default_value_to_label(self) -> "OptionIn":
        # value is optional for callers, but the whole stack keys on it: the
        # inbox uses it as each option's identity (without it a multi_select
        # checks every option at once) and answer validation builds its valid
        # set from non-null values. Fall back to the label so every option has
        # a distinct, non-null value.
        if not (self.value and self.value.strip()):
            self.value = self.label
        return self


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
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _dedupe_option_values(self) -> "DecisionIn":
        # Per-option defaulting makes value fall back to label, but two options
        # sharing a label (e.g. both "Other") would then collide and the inbox
        # would treat them as one identity again. Keep the first occurrence and
        # suffix later collisions so every option's value stays distinct; labels
        # are untouched (display) since value is the identity the stack keys on.
        seen: set[str] = set()
        for opt in self.options:
            base = opt.value or ""
            value = base
            n = 2
            while value in seen:
                value = f"{base} ({n})"
                n += 1
            opt.value = value
            seen.add(value)
        return self


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
        metadata=body.metadata,
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
    await _apply_app_grant(request, updated, body.value)
    await _route_answer_to_agent(updated, body.value)
    return updated


async def _apply_app_grant(request: Request, decision: dict, value) -> None:
    """Side effect for an app-grant consent Decision: write the per-capability
    grant decisions to the app_grants ledger. The decision's metadata carries
    {kind: "app_grant", app_id, capabilities}; for the multi_select consent card
    the answer is the list of granted capability values, so the rest are denied.
    Best-effort: the answer is already persisted, so a grant-store hiccup must
    not fail the answer."""
    meta = decision.get("metadata") or {}
    if meta.get("kind") != "app_grant":
        return
    grants = getattr(request.app.state, "app_grants", None)
    app_id = meta.get("app_id")
    caps = meta.get("capabilities") or []
    if grants is None or not app_id:
        return
    user_id = decision.get("user_id") or ""
    granted = set(value if isinstance(value, list) else [value])
    try:
        for cap in caps:
            await grants.set_decision(
                user_id, app_id, cap,
                decision="granted" if cap in granted else "denied",
            )
    except Exception:
        # Best-effort: the answer is already persisted, so a ledger write must
        # not fail the request. Log it rather than swallow silently so a broken
        # grant write is diagnosable (the user can re-grant via the Permissions
        # app).
        logger.warning(
            "app_grant ledger write failed for app %s (decision %s)",
            app_id, decision.get("id"), exc_info=True,
        )
