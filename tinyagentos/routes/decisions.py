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

from tinyagentos.auth_context import CurrentUser
from tinyagentos.decisions.decision_store import DECISION_TYPES, PRIORITIES
from tinyagentos.device_auth import current_user_or_device
from tinyagentos.events.bus import SystemEvent

logger = logging.getLogger(__name__)

router = APIRouter()

# Answer-routing: when a decision is answered, post the answer back to the
# asking agent on the A2A bus so off-session agents (taOSmd-dev, owl lanes,
# deployed agents) can pick it up. @taOS-dev polls the API instead.
_DEFAULT_BUS_URL = "http://127.0.0.1:7900"
_ANSWER_THREAD = "decisions"

# Decision metadata kinds that carry privileged grant authority.  The human
# answer path must refuse these from device bearers (a phone is a notification
# surface, not an approval channel), and the agent mirror path must refuse them
# so an agent cannot self-approve a gate it created.  Keeping the list in one
# place prevents drift between the two call sites and the applier functions.
GATE_DECISION_KINDS = ("execution_gate", "delegation_gate", "app_grant")

# Server-stamped provenance marker for gate decisions (tsk-mul5pa).  The
# public create path strips this key so an API caller can never self-stamp a
# privileged gate; only the internal raisers (peer-inbox delegation dispatch,
# execution-gate raiser, device-pairing raiser, app-grant flow) set it.  Every
# _apply_*_grant handler refuses to act when it is absent, so a caller-supplied
# metadata.kind can never mint a grant on approval.
SERVER_RAISED_KEY = "_server_raised"


def _bus_url() -> str:
    return os.environ.get("TAOS_A2A_BUS_URL", _DEFAULT_BUS_URL).rstrip("/")


def _answer_text(decision: dict, value) -> str:
    """Render a select answer using its option labels; pass others through."""
    opts = {o.get("value"): o.get("label") for o in (decision.get("options") or [])}
    vals = value if isinstance(value, list) else [value]
    return ", ".join(str(opts.get(v, v)) for v in vals)


async def _route_answer_to_agent(decision: dict, value, note: str | None = None) -> None:
    """Best-effort: post the recorded answer back to the asking agent on the
    A2A bus. Never raises; the answer is already persisted and the agent can
    also poll GET /api/decisions/{id}."""
    agent = (decision.get("from_agent") or "").strip()
    if not agent.startswith("@"):
        return
    text = _answer_text(decision, value)
    if note:
        text = f"{text} (note: {note})"
    body = (
        f"{agent} decision {decision.get('id')} answered: "
        f"{decision.get('question', '')} -> {text}"
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


async def _publish_answer_event(request: Request, decision: dict) -> None:
    """Best-effort: push a ``decision.answered`` event to SSE clients so every open
    surface (the chat thread that rendered the block, plus the Decisions app)
    updates live without a refresh.

    Published to the owner's ``user:<id>`` channel, NOT broadcast: the payload is
    the full decision record and the list/get routes scope decisions per user, so
    a broadcast would hand every connected user (and, via the replay buffer,
    every late connector) other users' decision content. An ownerless decision
    is skipped -- live update is best-effort and the apps refetch on focus.
    It never runs consent side effects and never breaks the answer if the
    emitter is absent (e.g. on hosts where the event bus has not been started)
    or fails.
    """
    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        return
    payload = dict(decision) if isinstance(decision, dict) else {"id": decision.get("id")}
    payload["decision_id"] = payload.get("id")
    owner = str(payload.get("user_id") or "").strip()
    if not owner:
        return
    try:
        await bus.publish_to(f"user:{owner}", SystemEvent(
            kind="decision.answered",
            source="decisions",
            targets=["user"],
            payload=payload,
        ))
    except Exception:
        logger.warning(
            "decision.answered SSE broadcast failed for %s",
            decision.get("id"),
            exc_info=True,
        )


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
    other_value: str | None = None
    note: str | None = None
    answered_by: str = ""
    source: str = "in_app"


@dataclass
class _DecisionActor:
    """Who is creating a decision, and who should decide it."""

    kind: str                # "agent" or "user"
    from_agent: str | None   # agent handle (agent path); None on the human path
    decider_user_id: str     # the human the decision is addressed to
    is_admin: bool           # human-path admin flag; always False for agents


async def _authenticate_request(request: Request) -> str | None:
    """Verify a registry JWT bearer identity BEFORE body parsing.

    FastAPI evaluates route dependencies before the body model, so probing the
    bearer here guarantees a bad/invalid token raises 401 before Pydantic body
    validation can surface a 422. Without this, a garbage bearer token paired
    with a malformed body returned 422 (field errors) and only a valid body ever
    reached the 401 -- so a dead token falsely looked authorized.

    Returns the canonical_id for a present-and-valid Bearer registry JWT, or
    None when no Bearer is present (the human/session path is handled later by
    _resolve_decision_actor). Any Bearer that is not a valid registry JWT
    (malformed, bad signature, inactive agent) raises 401/403 and never reaches
    the body.
    """
    from tinyagentos.agent_token_auth import check_agent_identity
    return await check_agent_identity(request)


async def _resolve_decision_actor(
    request: Request, project_id: str | None, agent_cid: str | None
) -> _DecisionActor:
    """Authorize the caller as either a granted agent or a human session.

    ``agent_cid`` is the agent's canonical_id, identity-verified up front by the
    _authenticate_request dependency, which runs BEFORE body parsing. So a bad
    bearer token 401s before Pydantic body validation can surface a 422 -- closing
    the auth-leak where a garbage token plus a malformed body returned 422 and
    falsely looked authorized.

    When ``agent_cid`` is None no Bearer was presented and the human/session
    path is taken: the session user (already resolved by the auth middleware)
    creates the decision and is the decider.

    Agent path (bearer token with a decisions_write grant): the decision is
    attributed to the authenticated agent, and the decider is resolved from the
    target - the project owner for a project decision, or the instance admin for
    an OS-level (null-project) decision. A global grant authorizes only
    null-project posts; a per-project grant authorizes only that project.

    Human path (session, no bearer token): unchanged - the session user creates
    and is the decider.
    """
    from tinyagentos.agent_token_auth import check_agent_scope_for_project

    if agent_cid is not None:
        # Bearer present and identity verified by the dependency; now enforce
        # the grant + project binding. check_agent_scope_for_project re-verifies
        # the token identity idempotently (as the answer/agent route already
        # does) and raises 401/403 on a missing or project-mismatched grant.
        cid = await check_agent_scope_for_project(request, "decisions_write", project_id)
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
async def create_decision(
    request: Request,
    body: DecisionIn,
    agent_cid: str | None = Depends(_authenticate_request),
):
    if body.type not in DECISION_TYPES:
        return JSONResponse({"error": f"type must be one of {DECISION_TYPES}"}, status_code=400)
    if body.priority not in PRIORITIES:
        return JSONResponse({"error": f"priority must be one of {PRIORITIES}"}, status_code=400)
    if body.type in ("single_select", "multi_select") and not body.options:
        return JSONResponse({"error": "select types require options"}, status_code=400)

    actor = await _resolve_decision_actor(request, body.project_id, agent_cid)
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

    # tsk-mul5pa: an API caller cannot stamp server provenance.  Strip the
    # marker before persisting so the _apply_*_grant handlers refuse any gate
    # decision whose metadata was supplied over the wire.
    metadata = dict(body.metadata) if isinstance(body.metadata, dict) else {}
    metadata.pop(SERVER_RAISED_KEY, None)
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
        metadata=metadata,
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
                data={
                    "decision_type": body.type,
                    "options": [o.model_dump() for o in body.options],
                    "decision_id": decision["id"],
                },
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
    user: CurrentUser = Depends(current_user_or_device),
):
    store = request.app.state.decision_store
    # Non-admins see only their own decisions; admins see all.
    uid = None if user.is_admin else user.user_id
    # Only forward project_id when the caller specified one; an absent query
    # param means "no project filter", which is the store's default (_UNSET).
    kwargs: dict[str, object] = {"status": status, "user_id": uid, "limit": limit}
    if project_id is not None:
        kwargs["project_id"] = project_id
    items = await store.list(**kwargs)
    return {"items": items}


# Agent-decisions list (filtered by the asking agent). Must be registered
# BEFORE the parameterised GET /api/decisions/{decision_id} so the literal
# "agent" segment is not captured as a decision_id.
@router.get("/api/decisions/agent")
async def list_decisions_as_agent(request: Request):
    """Agent-facing list: filter by from_agent (the asking agent).  Only
    returns decisions whose project the agent holds an active decisions_write
    grant for.  A global (null-project) grant means null-project decisions
    only, matching _resolve_decision_actor's posting rule; otherwise, only
    decisions from allowed projects are returned.  The store layer enforces
    the from_agent binding so there is no cross-agent leakage.

    LIMIT INTERACTION, and it is not uniform across the three paths: the global
    and single-project paths push the project filter INTO the store query, so the
    limit applies AFTER scoping (issue #2194).  The two-or-more-project path
    cannot express that as one query, so it still fetches up to `limit` rows for
    the agent and filters in Python afterwards; an agent holding grants on
    several projects and carrying more than `limit` decisions in total can still
    lose allowed-project rows to the limit there."""
    from datetime import datetime, timezone
    from tinyagentos.agent_token_auth import check_agent_scope, _grant_unexpired

    canonical_id = await check_agent_scope(request, "decisions_write")
    if canonical_id is None:
        return JSONResponse({"error": "agent identity not found"}, status_code=401)

    # Collect project IDs the agent has active decisions_write grants for.
    grants_store = request.app.state.agent_grants
    now = datetime.now(timezone.utc)
    grants = await grants_store.list_grants(canonical_id)
    allowed_projects: set[str | None] = {
        g.get("project_id")
        for g in grants
        if g["scope"] == "decisions_write" and _grant_unexpired(g.get("expires_at"), now)
    }

    store = request.app.state.decision_store
    # A global (null-project) grant means null-project decisions only,
    # matching _resolve_decision_actor's posting rule.
    # The project filter is pushed into the store query so the limit applies
    # AFTER scoping (issue #2194) -- for the global and single-project paths.
    # The multi-project fallback below still filters after the fetch.
    if None in allowed_projects:
        # Global grant: only null-project decisions
        items = await store.list(from_agent=canonical_id, project_id=None, limit=500)
    else:
        # Per-project grants: only those projects.
        # Push the project filter into the store query so the limit applies
        # after scoping. If there is exactly one allowed project, use it
        # directly; otherwise fall back to Python filtering after the fetch.
        if len(allowed_projects) == 1:
            project_id = allowed_projects.pop()
            items = await store.list(from_agent=canonical_id, project_id=project_id, limit=500)
        else:
            items = await store.list(from_agent=canonical_id, limit=500)
            items = [d for d in items if d.get("project_id") in allowed_projects]
    return {"items": items}


@router.get("/api/decisions/{decision_id}")
async def get_decision(decision_id: str, request: Request, user: CurrentUser = Depends(current_user_or_device)):
    # Session-only path: human reading their own or admin reading any
    store = request.app.state.decision_store
    d = await store.get(decision_id)
    if d is None or (not user.is_admin and d["user_id"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@router.get("/api/decisions/{decision_id}/agent")
async def get_decision_as_agent(decision_id: str, request: Request):
    # JWT-first: validate the bearer token before touching the decision
    # store so invalid tokens cannot probe decision existence via timing.
    from tinyagentos.agent_token_auth import (
        check_agent_identity,
        check_agent_scope_for_project,
        PROJECT_SCOPE_MISMATCH_DETAIL,
    )
    caller = await check_agent_identity(request)
    if caller is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    store = request.app.state.decision_store
    d = await store.get(decision_id)
    if d is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Project-scoped (least privilege): the agent must hold decisions_write
    # on the decision's OWN project, not just any project.  5d207ec
    # mistakenly swapped this for project-agnostic check_agent_scope,
    # letting any decisions_write grant on ANY project authorize read
    # on EVERY project.  Restored from 6410e3c.
    # Collapse PROJECT_SCOPE_MISMATCH to 404 so the route is not an
    # existence oracle (never distinguish 403-vs-404 to a caller).
    try:
        cid = await check_agent_scope_for_project(
            request, "decisions_write", d.get("project_id")
        )
    except HTTPException as exc:
        if exc.status_code == 403 and exc.detail == PROJECT_SCOPE_MISMATCH_DETAIL:
            return JSONResponse({"error": "not found"}, status_code=404)
        raise
    if cid is None or d.get("from_agent") != cid:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@router.get("/api/decisions/{decision_id}/history")
async def decision_history(decision_id: str, request: Request, user: CurrentUser = Depends(current_user_or_device)):
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
async def answer_decision(decision_id: str, body: AnswerIn, request: Request, user: CurrentUser = Depends(current_user_or_device)):
    store = request.app.state.decision_store
    existing = await store.get(decision_id)
    # Authorization check: humans can answer decisions they own or admins; agents are
    # covered by the _AGENT_TOKEN_PATHS allowlist so they never reach this route.
    if existing is None or (not user.is_admin and existing["user_id"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)

    # Device bearers must not answer gate-kind decisions: the phone is a
    # notification surface, not an approval channel for privileged grants.
    # INVARIANT (c) in device_auth.py: request.state.user_id is None on this
    # path, so a falsy user_id means the caller authenticated as a device.
    if not getattr(request.state, "user_id", None):
        meta = existing.get("metadata") or {}
        gate_kind = meta.get("kind") if isinstance(meta, dict) else None
        if gate_kind in GATE_DECISION_KINDS:
            return JSONResponse(
                {"error": "gate decisions cannot be answered by a device bearer"},
                status_code=409,
            )

    # For select types, the answer may reference the declared options or use
    # the free-text Other path.  A stale or malformed client cannot record an
    # arbitrary value: explicit option values are still validated against the
    # declared list so a typo is rejected; the free-text path is accepted only
    # when flagged via other_value.
    dtype = existing.get("type")
    if dtype in ("single_select", "multi_select"):
        if body.other_value is not None:
            # Other / free-text path: validate any explicit option values but
            # accept the free-text value as-is.
            if dtype == "single_select":
                if body.value is not None and isinstance(body.value, str) and body.value.strip():
                    return JSONResponse(
                        {"error": "cannot combine value with other_value"},
                        status_code=400,
                    )
            else:
                vals = body.value if isinstance(body.value, list) else None
                if vals is None:
                    return JSONResponse({"error": "invalid answer value shape"}, status_code=400)
                valid = {
                    o.get("value")
                    for o in (existing.get("options") or [])
                    if o.get("value") is not None
                }
                if valid and any(v not in valid for v in vals):
                    return JSONResponse({"error": "answer must be a subset of the options"}, status_code=400)
        else:
            # Option-only path: existing strict validation.
            valid = {
                o.get("value")
                for o in (existing.get("options") or [])
                if o.get("value") is not None
            }
            if valid:
                try:
                    if dtype == "single_select":
                        if body.value not in valid:
                            return JSONResponse({"error": "answer is not one of the options"}, status_code=400)
                    else:
                        vals = body.value if isinstance(body.value, list) else None
                        if vals is None or any(v not in valid for v in vals):
                            return JSONResponse({"error": "answer must be a subset of the options"}, status_code=400)
                except TypeError:
                    return JSONResponse({"error": "invalid answer value shape"}, status_code=400)

    # Source is derived server-side from the authenticated route, never trusted
    # from the request body.  The human path always records in_app; the agent
    # mirror path (answer_decision_as_agent) always records mirrored_from_chat.
    # A caller setting source=mirrored_from_chat on the human path is spoofing
    # the audit trail and must be rejected.
    if body.source == "mirrored_from_chat":
        return JSONResponse(
            {"error": "source must not be mirrored_from_chat on this endpoint"},
            status_code=400,
        )
    source = "in_app"
    answered_by = body.answered_by or user.user_id or "user"
    dtype = existing.get("type")
    if dtype == "single_select" and body.other_value is not None:
        stored_value = body.other_value.strip()
    elif dtype == "multi_select" and body.other_value is not None:
        vals = list(body.value) if isinstance(body.value, list) else []
        stored_value = [*vals, body.other_value.strip()]
    else:
        stored_value = body.value
    updated = await store.answer(
        decision_id, stored_value, answered_by, source=source,
        other_value=body.other_value, note=body.note,
    )
    if updated is None:
        return JSONResponse({"error": "already answered or not pending"}, status_code=409)
    # Broadcast a live event so open chat threads and the Decisions app resolve
    # the card in place. Best-effort: never break the recorded answer on a
    # notification hiccup.
    await _publish_answer_event(request, updated)
    # Each kind-specific handler runs its side effect and returns True if it
    # already routed a reply to the asking agent (a more informative,
    # kind-specific message). Only route the generic answer when none did, so
    # a gated decision does not send the agent two messages.
    routed_app = await _apply_app_grant(request, updated, stored_value)
    routed_exec = await _apply_execution_grant(request, updated, stored_value)
    routed_deleg = await _apply_delegation_grant(request, updated, stored_value)
    routed_pair = await _apply_device_pairing_grant(request, updated, stored_value)
    if not (routed_app or routed_exec or routed_deleg or routed_pair):
        await _route_answer_to_agent(updated, stored_value, note=body.note)
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
    if meta.get(SERVER_RAISED_KEY) is not True:
        logger.warning(
            "gate decision %s refused: missing server provenance (kind=%r)",
            decision.get("id"), meta.get("kind"),
        )
        return False
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


async def _apply_device_pairing_grant(request: Request, decision: dict, value) -> bool:
    """Side effect for a device_pairing Decision (taOS S4e): the decision's
    metadata carries {kind: "device_pairing", pair_request_id}. Approving it
    mints the device via DeviceStore.register (bound to the user the Decision
    was addressed to -- F1) and atomically transitions THAT pair request to
    accepted (F2); denying transitions it to denied. Expiry is enforced here,
    at approve time, not merely hidden from the poll (F6).

    Mirrors ``_apply_app_grant``: the answer is already persisted, so a store
    hiccup must not fail the answer. Returns True when the decision was a
    device_pairing one (there is no asking agent to route a reply to -- the
    requesting device learns the outcome by polling its pair request).

    Double-mint safety (F5): the Decisions store's ``answer`` is itself atomic
    (a second answer 409s before any applier runs), so this applier runs at
    most once per decision, and creation makes exactly one decision per pair
    request. Belt-and-braces on top: if the pair request's conditional
    pending->accepted UPDATE reports the row was already decided, the freshly
    minted device is revoked rather than left dangling."""
    meta = decision.get("metadata") or {}
    if meta.get(SERVER_RAISED_KEY) is not True:
        logger.warning(
            "gate decision %s refused: missing server provenance (kind=%r)",
            decision.get("id"), meta.get("kind"),
        )
        return False
    if meta.get("kind") != "device_pairing":
        return False
    store = getattr(request.app.state, "device_pair_requests", None)
    pair_request_id = meta.get("pair_request_id")
    if store is None or not pair_request_id:
        return False
    decided_by = decision.get("user_id") or "user"
    try:
        record = await store.get(pair_request_id)
        if record is None:
            return True
        if value != "approve":
            await store.set_decision(pair_request_id, "denied", decided_by=decided_by)
            return True
        from tinyagentos.device_pair_requests_store import _live_status
        if _live_status(record) == "expired":
            # F6: an approval of an already-expired request persists the expiry
            # instead of minting.
            await store.set_decision(pair_request_id, "expired", decided_by=decided_by)
            return True
        device_store = getattr(request.app.state, "device_store", None)
        if device_store is None:
            logger.warning(
                "device pairing approve: device_store missing (decision %s)",
                decision.get("id"),
            )
            return True
        # F1: the device is bound to the user the Decision was addressed to,
        # never to anything the unauthenticated creator supplied.
        device = await device_store.register(
            user_id=decided_by,
            platform=record.get("platform") or "",
            display_name=record.get("display_name") or "",
        )
        updated = await store.set_decision(
            pair_request_id, "accepted",
            device_id=device["device_id"], decided_by=decided_by,
        )
        if updated is None:
            # Lost a (should-be-impossible) race: request already decided.
            # Revoke the fresh mint so no orphan credential survives.
            await device_store.revoke(device["device_id"])
    except Exception:
        logger.warning(
            "device pairing grant failed for decision %s", decision.get("id"), exc_info=True,
        )
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
    if meta.get(SERVER_RAISED_KEY) is not True:
        logger.warning(
            "gate decision %s refused: missing server provenance (kind=%r)",
            decision.get("id"), meta.get("kind"),
        )
        return False
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


@router.post("/api/decisions/{decision_id}/answer/agent")
async def answer_decision_as_agent(
    decision_id: str,
    request: Request,
    body: AnswerIn,
    caller: str | None = Depends(_authenticate_request),
):
    """Agent mirror: only the asking agent can answer its own decision.

    Verifies the registry JWT holds decisions_write for the decision's project,
    checks from_agent ownership, records the answer with source=mirrored_from_chat
    and the agent's canonical id as the mirroring actor, then pushes to the
    asking agent via A2A.  Consent side effects (app/execution/delegation grants)
    are intentionally NOT run on this path for gate-kind decisions (409).

    The bearer identity is verified by the _authenticate_request dependency
    (which runs before body parsing) so a bad token 401s before a 422 can leak
    and no invalid token can probe decision existence via timing.
    """
    if caller is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    store = request.app.state.decision_store
    existing = await store.get(decision_id)
    if existing is None or existing.get("status") != "pending":
        return JSONResponse({"error": "not found"}, status_code=404)

    # Project-scoped (least privilege): the agent must hold decisions_write
    # on the decision's OWN project, not just any project.  5d207ec
    # mistakenly swapped this for project-agnostic check_agent_scope,
    # letting any decisions_write grant on ANY project authorize answer
    # on EVERY project.  Restored from 6410e3c.
    # Collapse PROJECT_SCOPE_MISMATCH to 404 so the route is not an
    # existence oracle (never distinguish 403-vs-404 to a caller).
    from tinyagentos.agent_token_auth import (
        check_agent_scope_for_project,
        PROJECT_SCOPE_MISMATCH_DETAIL,
    )
    try:
        canonical_id = await check_agent_scope_for_project(
            request, "decisions_write", existing.get("project_id")
        )
    except HTTPException as exc:
        if exc.status_code == 403 and exc.detail == PROJECT_SCOPE_MISMATCH_DETAIL:
            return JSONResponse({"error": "not found"}, status_code=404)
        raise
    if canonical_id is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Verify ownership: only the asking agent can answer its own decision.
    if existing.get("from_agent") != canonical_id:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Gate-kind decisions must NOT be mirror-approvable by an agent.
    # An agent that creates a privileged gate decision (execution_gate,
    # delegation_gate, app_grant) and then mirrors "approve" through its
    # own answer/agent endpoint would be able to dismiss the pending gate
    # card from the owner's inbox without the owner ever seeing it.
    meta = (existing.get("metadata") or {})
    gate_kind = meta.get("kind") if isinstance(meta, dict) else None
    if gate_kind in GATE_DECISION_KINDS:
        return JSONResponse(
            {"error": "gate decisions cannot be answered by the asking agent"},
            status_code=409,
        )

    # Validate select-type answers against declared options (same as human path).
    dtype = existing.get("type")
    if dtype in ("single_select", "multi_select"):
        if body.other_value is not None:
            if dtype == "single_select":
                if body.value is not None and isinstance(body.value, str) and body.value.strip():
                    return JSONResponse(
                        {"error": "cannot combine value with other_value"},
                        status_code=400,
                    )
            else:
                vals = body.value if isinstance(body.value, list) else None
                if vals is None:
                    return JSONResponse({"error": "invalid answer value shape"}, status_code=400)
                valid = {
                    o.get("value")
                    for o in (existing.get("options") or [])
                    if o.get("value") is not None
                }
                if valid and any(v not in valid for v in vals):
                    return JSONResponse({"error": "answer must be a subset of the options"}, status_code=400)
        else:
            valid = {
                o.get("value")
                for o in (existing.get("options") or [])
                if o.get("value") is not None
            }
            if valid:
                try:
                    if dtype == "single_select":
                        if body.value not in valid:
                            return JSONResponse({"error": "answer is not one of the options"}, status_code=400)
                    else:
                        vals = body.value if isinstance(body.value, list) else None
                        if vals is None or any(v not in valid for v in vals):
                            return JSONResponse({"error": "answer must be a subset of the options"}, status_code=400)
                except TypeError:
                    return JSONResponse({"error": "invalid answer value shape"}, status_code=400)

    # Agent mirrors are always from chat; record the canonical agent id as the
    # mirroring actor (not "user") so the audit trail is complete.
    source = "mirrored_from_chat"
    answered_by = canonical_id
    dtype = existing.get("type")
    if dtype == "single_select" and body.other_value is not None:
        stored_value = body.other_value.strip()
    elif dtype == "multi_select" and body.other_value is not None:
        vals = list(body.value) if isinstance(body.value, list) else []
        stored_value = [*vals, body.other_value.strip()]
    else:
        stored_value = body.value
    updated = await store.answer(
        decision_id, stored_value, answered_by, source=source,
        other_value=body.other_value, note=body.note,
    )
    if updated is None:
        return JSONResponse({"error": "already answered or not pending"}, status_code=409)

    # Broadcast live so open surfaces (chat + Decisions app) resolve in place.
    await _publish_answer_event(request, updated)

    # Route the answer to the A2A bus so the asking agent can pick it up.
    # Consent side effects (app/execution/delegation grants) are intentionally
    # NOT run on the agent mirror path: an agent must not be able to create a
    # privileged decision and then self-approve the consent via its own mirror
    # endpoint.  Only the human answer path runs consent side effects.
    await _route_answer_to_agent(updated, stored_value, note=body.note)
    return updated


async def _apply_app_grant(request: Request, decision: dict, value) -> bool:
    """Side effect for an app-grant consent Decision: write the per-capability
    grant decisions to the app_grants ledger. The decision's metadata carries
    {kind: "app_grant", app_id, capabilities}; for the multi_select consent card
    the answer is the list of granted capability values, so the rest are denied.
    Best-effort: the answer is already persisted, so a grant-store hiccup must
    not fail the answer. Returns False: app grants do not route an agent reply,
    so the caller sends the generic answer."""
    meta = decision.get("metadata") or {}
    if meta.get(SERVER_RAISED_KEY) is not True:
        logger.warning(
            "gate decision %s refused: missing server provenance (kind=%r)",
            decision.get("id"), meta.get("kind"),
        )
        return False
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
