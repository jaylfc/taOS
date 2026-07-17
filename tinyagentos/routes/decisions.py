"""Decisions API: the human-in-the-loop inbox.

An agent posts a decision (a choice it needs from the user); it queues until
answered. Consent and explicitly-blocking decisions raise a higher-priority
notification; everything else is a normal badge + notification.
"""

from __future__ import annotations

import logging
import os

from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
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


@dataclass
class _DecisionActor:
    """Who is creating a decision, and who should decide it."""

    kind: str                # "agent" or "user"
    from_agent: str | None   # agent handle (agent path); None on the human path
    decider_user_id: str     # the human the decision is addressed to
    is_admin: bool           # human-path admin flag; always False for agents


async def _resolve_decision_actor(request: Request, project_id: str | None) -> _DecisionActor:
    """Authorize the caller as either a granted agent or a human session.

    Agent path (bearer token with a decisions_write grant): the decision is
    attributed to the authenticated agent, and the decider is resolved from the
    target - the project owner for a project decision, or the instance admin for
    an OS-level (null-project) decision. A global grant authorizes only
    null-project posts; a per-project grant authorizes only that project.

    Human path (session, no bearer token): unchanged - the session user creates
    and is the decider.
    """
    from tinyagentos.agent_token_auth import check_agent_scope_for_project

    # Returns None when there is no Authorization header (a cookie-only human),
    # the canonical_id when the agent holds the grant, or raises 401/403.
    cid = await check_agent_scope_for_project(request, "decisions_write", project_id)
    if cid is not None:
        if project_id is not None:
            project = await request.app.state.project_store.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=400, detail="project_id not found")
            decider = project.get("user_id") or ""
            if not decider:
                raise HTTPException(status_code=409, detail="project has no owner to decide")
        else:
            admins = [u for u in request.app.state.auth.list_users() if u.get("is_admin")]
            if not admins:
                raise HTTPException(status_code=409, detail="no admin to receive an OS-level decision")
            decider = admins[0]["id"]
        return _DecisionActor(kind="agent", from_agent=cid, decider_user_id=decider, is_admin=False)

    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authentication required")
    is_admin = bool(getattr(request.state, "is_admin", False))
    return _DecisionActor(kind="user", from_agent=None, decider_user_id=uid, is_admin=is_admin)


@router.post("/api/decisions")
async def create_decision(body: DecisionIn, request: Request):
    if body.type not in DECISION_TYPES:
        return JSONResponse({"error": f"type must be one of {DECISION_TYPES}"}, status_code=400)
    if body.priority not in PRIORITIES:
        return JSONResponse({"error": f"priority must be one of {PRIORITIES}"}, status_code=400)
    if body.type in ("single_select", "multi_select") and not body.options:
        return JSONResponse({"error": "select types require options"}, status_code=400)

    actor = await _resolve_decision_actor(request, body.project_id)
    # Agent identity is authoritative; a human may set from_agent in the body.
    from_agent = actor.from_agent or body.from_agent

    store = request.app.state.decision_store

    # L1 revisit/supersede: a decision may replace an earlier one going forward.
    # A human supersedes their own (or any, if admin); an agent supersedes only
    # decisions it raised itself.
    parent_id = body.parent_decision_id
    if parent_id:
        parent = await store.get(parent_id)
        if parent is None:
            return JSONResponse({"error": "parent_decision_id not found"}, status_code=400)
        if actor.kind == "user":
            if not actor.is_admin and parent["user_id"] != actor.decider_user_id:
                return JSONResponse({"error": "parent_decision_id not found"}, status_code=400)
        elif parent.get("from_agent") != from_agent:
            return JSONResponse({"error": "parent_decision_id not found"}, status_code=400)

    decision = await store.create(
        from_agent=from_agent,
        question=body.question,
        type=body.type,
        options=[o.model_dump() for o in body.options],
        context=body.context,
        priority=body.priority,
        project_id=body.project_id,
        user_id=actor.decider_user_id,
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
                message=f"{from_agent} needs a decision: {body.question[:120]}",
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
    # Each kind-specific handler runs its side effect and returns True if it
    # already routed a reply to the asking agent (a more informative,
    # kind-specific message). Only route the generic answer when none did, so
    # a gated decision does not send the agent two messages.
    routed_app = await _apply_app_grant(request, updated, body.value)
    routed_exec = await _apply_execution_grant(request, updated, body.value)
    routed_deleg = await _apply_delegation_grant(request, updated, body.value)
    if not (routed_app or routed_exec or routed_deleg):
        await _route_answer_to_agent(updated, body.value)
    return updated


async def _apply_execution_grant(request: Request, decision: dict, value) -> bool:
    """Side effect for an execution-gate Decision (agent governance #160 slice
    1): the decision's metadata carries {kind: "execution_gate", agent_name,
    action_class, tool}. Approving it writes a short-lived execution grant so
    the agent's retry of that exact action passes the policy gate without
    asking again; either way the agent is notified so it can retry or stop.
    Mirrors ``_apply_app_grant``: the answer is already persisted, so a grant-
    store hiccup must not fail the answer."""
    meta = decision.get("metadata") or {}
    if meta.get("kind") != "execution_gate":
        return False
    policies = getattr(request.app.state, "execution_policies", None)
    agent_name = meta.get("agent_name")
    action_class = meta.get("action_class")
    if policies is None or not agent_name or not action_class:
        return False
    approved = value == "approve"
    granted = False
    try:
        if approved:
            await policies.add_grant(agent_name, action_class, decision.get("id"))
            granted = True
    except Exception:
        # Best-effort: the answer is already persisted, so a grant-store write
        # must not fail the request. Log it rather than swallow silently so a
        # broken grant write is diagnosable.
        logger.warning(
            "execution grant write failed for agent %s (decision %s)",
            agent_name, decision.get("id"), exc_info=True,
        )
    # decision["from_agent"] is already agent_name (set when the gate created
    # this decision in skill_exec.py); reuse the existing answer-routing path.
    # Only tell the agent it may retry when the grant actually persisted -- if
    # the write failed, the gate will re-prompt on retry, so say so honestly.
    if not approved:
        reply = "your action was denied"
    elif granted:
        reply = "you may retry the action now"
    else:
        reply = "your action was approved, but saving the grant failed - please retry"
    await _route_answer_to_agent(decision, reply)
    return True


async def _apply_delegation_grant(request: Request, decision: dict, value) -> bool:
    """Side effect for a delegation-gate Decision (#161 gated delegation): the
    decision's metadata carries {kind: "delegation_gate", from_agent, to_agent,
    task_id, task_title, note}. Approving it writes a short-lived delegate
    grant (so a future delegation by the same agent doesn't re-ask until the
    grant expires) AND completes THIS delegation (assign the task + notify
    to_agent); denying just routes the answer. Mirrors
    ``_apply_execution_grant``: the answer is already persisted, so a
    grant-store or delegation-completion hiccup must not fail the answer."""
    meta = decision.get("metadata") or {}
    if meta.get("kind") != "delegation_gate":
        return False
    policies = getattr(request.app.state, "execution_policies", None)
    from_agent = meta.get("from_agent")
    to_agent = meta.get("to_agent")
    if policies is None or not from_agent or not to_agent:
        return False
    approved = value == "approve"
    completed = False
    if approved:
        try:
            from tinyagentos.routes.delegation import complete_delegation
            await complete_delegation(
                request,
                from_agent=from_agent,
                to_agent=to_agent,
                task_id=meta.get("task_id"),
                task_title=meta.get("task_title"),
                project_id=decision.get("project_id"),
                note=meta.get("note") or "",
            )
            completed = True
        except Exception:
            logger.warning(
                "delegation completion failed for decision %s", decision.get("id"), exc_info=True,
            )
        # Grant the delegate capability only once THIS delegation actually
        # completed. Writing it before completion leaked a grant on failure: a
        # later delegation by the same agent would then skip approval while the
        # original assignment had silently never happened.
        if completed:
            try:
                await policies.add_grant(from_agent, "delegate", decision.get("id"))
            except Exception:
                logger.warning(
                    "delegate grant write failed for agent %s (decision %s)",
                    from_agent, decision.get("id"), exc_info=True,
                )
    # Tell the agent the truth: only claim the task was assigned when the
    # completion actually succeeded, so a failed assign is not reported as done.
    if not approved:
        reply = "delegation denied"
    elif completed:
        reply = "delegation approved - the task has been assigned"
    else:
        reply = "delegation approved, but assigning the task failed - please retry"
    await _route_answer_to_agent(decision, reply)
    return True


async def _apply_app_grant(request: Request, decision: dict, value) -> bool:
    """Side effect for an app-grant consent Decision: write the per-capability
    grant decisions to the app_grants ledger. The decision's metadata carries
    {kind: "app_grant", app_id, capabilities}; for the multi_select consent card
    the answer is the list of granted capability values, so the rest are denied.
    Best-effort: the answer is already persisted, so a grant-store hiccup must
    not fail the answer. Returns False: app grants do not route an agent reply,
    so the caller sends the generic answer."""
    meta = decision.get("metadata") or {}
    if meta.get("kind") != "app_grant":
        return False
    grants = getattr(request.app.state, "app_grants", None)
    app_id = meta.get("app_id")
    caps = meta.get("capabilities") or []
    if grants is None or not app_id:
        return False
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
    return False
