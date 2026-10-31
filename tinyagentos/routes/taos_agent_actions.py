"""taOS Agent action gate (spec section 3.1).

POST /api/taos-agent/actions                 {kind, summary, payload} -> {action_id, state}
GET  /api/taos-agent/actions?state=pending   the caller's own actions (recovers a missed card)
POST /api/taos-agent/actions/{id}/confirm    human session only -> state confirmed
POST /api/taos-agent/actions/{id}/decline    human session only -> state declined
POST /api/taos-agent/actions/{id}/execute    409 unless confirmed; runs the kind's executor once
                                             (executing while it runs, then executed | failed)
PUT  /api/taos-agent/actions/policy          human session only, {kind, policy: ask|auto}

A new action under the default ``ask`` policy is stored pending and a
``taos_agent.action_pending`` event goes to the owner's ``user:<id>`` channel
(the chat confirm card). Under ``auto`` it is stored confirmed with
``confirmed_by=auto`` and no card is shown. The policy lives in the
``taos_agent`` preference blob of the signed-in user (keyed by their user
id, not the shared ``"user"`` key) as ``action_policy[kind]``, so one
account's ``auto`` never skips the card for another account.

Every request and every outcome (executed, declined, failed) is recorded as
an ``external_write`` row on the ``taos-agent`` trace (spec section 4).

Confirm, decline and the policy are refused (403) for anything but a signed-in
browser session: an agent credential can ask, it can never answer for the user
or change its own policy.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.events import SystemEvent
from tinyagentos.taos_agent_actions import (
    POLICY_ASK,
    POLICY_AUTO,
    STATE_CONFIRMED,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_PREF_NAMESPACE = "taos_agent"
_KIND_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"
_AUDIT_SLUG = "taos-agent"


class ActionCreate(BaseModel):
    kind: str = Field(pattern=_KIND_PATTERN)
    summary: str = Field(min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)


class PolicyUpdate(BaseModel):
    kind: str = Field(pattern=_KIND_PATTERN)
    policy: Literal["ask", "auto"]


def _view(row: dict) -> dict:
    return {
        "action_id": row["id"],
        "kind": row["kind"],
        "summary": row["summary"],
        "state": row["state"],
        "confirmed_by": row["confirmed_by"],
    }


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"error": detail}, status_code=status)


def _require_human(request: Request) -> str | JSONResponse:
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return _error(401, "authentication required")
    if getattr(request.state, "via", None) != "session":
        return _error(403, "only the signed-in user can answer an action")
    return uid


async def _audit(
    request: Request, row: dict, state: str, confirmed_by: str, *, url: str = "",
) -> None:
    """Best-effort ``external_write`` audit row; a trace failure never blocks the gate."""
    registry = getattr(request.app.state, "trace_registry", None)
    if registry is None:
        return
    payload = row.get("payload") or {}
    latency_ms = 0 if state in ("pending", STATE_CONFIRMED) else int(
        max(0.0, time.time() - row["created_at"]) * 1000
    )
    try:
        store = await registry.get(_AUDIT_SLUG)
        await store.record("external_write", payload={
            "target": row["kind"],
            "action": row["summary"],
            "user_id": row["user_id"],
            "repo": str(payload.get("repo", "")),
            "url": url,
            "diff_sha256": str(payload.get("diff_sha256", "")),
            "confirmed_by": confirmed_by,
            "action_id": row["id"],
            "latency_ms": latency_ms,
            "state": state,
        })
    except Exception:
        logger.exception("taos-agent action %s: audit row failed", row["id"])


async def _owned(request: Request, action_id: str, uid: str) -> dict | None:
    row = await request.app.state.taos_agent_actions.get(action_id)
    if row is None or row["user_id"] != uid:
        return None
    return row


async def _policy_for(request: Request, uid: str, kind: str) -> str:
    prefs = await request.app.state.desktop_settings.get_preference(uid, _PREF_NAMESPACE)
    policy = (prefs.get("action_policy") or {}).get(kind, POLICY_ASK)
    return policy if policy == POLICY_AUTO else POLICY_ASK


@router.get("/api/taos-agent/actions")
async def list_actions(
    request: Request,
    state: str | None = Query(default=None, pattern=r"^[a-z]{1,16}$"),
):
    """The caller's own actions. ``?state=pending`` lists confirm cards still open,
    so a card lost to a restart or a failed event publish can be shown again."""
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return _error(401, "authentication required")
    rows = await request.app.state.taos_agent_actions.list_for_user(uid, state=state)
    return {"actions": [_view(row) for row in rows]}


@router.post("/api/taos-agent/actions")
async def create_action(request: Request, body: ActionCreate):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return _error(401, "authentication required")
    auto = await _policy_for(request, uid, body.kind) == POLICY_AUTO
    row = await request.app.state.taos_agent_actions.create(
        user_id=uid, kind=body.kind, summary=body.summary, payload=body.payload, auto=auto,
    )
    await _audit(request, row, row["state"], row["confirmed_by"] or "pending")
    if not auto:
        event = SystemEvent(
            kind="taos_agent.action_pending",
            source="taos-agent",
            targets=[f"user:{uid}"],
            payload={"action_id": row["id"], "kind": row["kind"], "summary": row["summary"]},
        )
        try:
            await request.app.state.event_bus.publish_to(f"user:{uid}", event)
        except Exception:
            logger.exception("taos-agent action %s: confirm card event failed", row["id"])
    return _view(row)


async def _decide(request: Request, action_id: str, *, confirm: bool):
    uid = _require_human(request)
    if isinstance(uid, JSONResponse):
        return uid
    if await _owned(request, action_id, uid) is None:
        return _error(404, "action not found")
    row = await request.app.state.taos_agent_actions.decide(action_id, confirm=confirm)
    if row is None:
        return _error(409, "action is not pending")
    if not confirm:
        await _audit(request, row, "declined", "declined")
    return _view(row)


@router.post("/api/taos-agent/actions/{action_id}/confirm")
async def confirm_action(request: Request, action_id: str):
    return await _decide(request, action_id, confirm=True)


@router.post("/api/taos-agent/actions/{action_id}/decline")
async def decline_action(request: Request, action_id: str):
    return await _decide(request, action_id, confirm=False)


@router.post("/api/taos-agent/actions/{action_id}/execute")
async def execute_action(request: Request, action_id: str):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return _error(401, "authentication required")
    row = await _owned(request, action_id, uid)
    if row is None:
        return _error(404, "action not found")
    if row["state"] != STATE_CONFIRMED:
        return _error(409, f"action is {row['state']}, not confirmed")
    executor = request.app.state.taos_agent_action_executors.get(row["kind"])
    if executor is None:
        return _error(422, f"no executor for kind {row['kind']!r}")
    store = request.app.state.taos_agent_actions
    claimed = await store.claim_for_execution(action_id)
    if claimed is None:
        return _error(409, "action is not confirmed")
    try:
        result = await executor(claimed)
    except Exception as exc:
        logger.exception("taos-agent action %s: executor failed", action_id)
        await store.mark_failed(action_id, f"{type(exc).__name__}: {exc}")
        await _audit(request, claimed, "failed", claimed["confirmed_by"])
        return _error(502, "action failed")
    done = await store.mark_executed(action_id)
    url = result.get("url", "") if isinstance(result, dict) else ""
    await _audit(request, done, "executed", done["confirmed_by"], url=str(url or ""))
    return {**_view(done), "result": result}


@router.put("/api/taos-agent/actions/policy")
async def put_policy(request: Request, body: PolicyUpdate):
    uid = _require_human(request)
    if isinstance(uid, JSONResponse):
        return uid
    store = request.app.state.desktop_settings
    prefs = await store.get_preference(uid, _PREF_NAMESPACE)
    policy = dict(prefs.get("action_policy") or {})
    policy[body.kind] = body.policy
    prefs["action_policy"] = policy
    await store.save_preference(uid, _PREF_NAMESPACE, prefs)
    return {"action_policy": policy}
