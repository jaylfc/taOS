from __future__ import annotations

"""Routes for the external-agent consent loop (Phase 1).

POST   /api/agents/auth-requests                       — submit an access request (EXEMPT, no auth)
GET    /api/agents/auth-requests/{request_id}          — poll request status (EXEMPT, opaque-id cap)
POST   /api/agents/auth-requests/{request_id}/approve  — approve + mint identity (admin only)
POST   /api/agents/auth-requests/{request_id}/deny     — deny the request (admin only)
GET    /api/agents/auth-requests                       — list pending requests (admin only)

The two public endpoints (create + status poll) are added to auth_middleware.EXEMPT_PATHS
so unauthenticated external agents can reach them.  The opaque UUID request_id acts as a
capability token for the poll endpoint — only the caller who received the id can poll it.

Security notes
--------------
* The token field is returned ONLY on status == 'accepted'.
* Admin gate on approve / deny / list — checked via current_user + is_admin flag.
* Abuse cap: at most _PENDING_CAP pending requests per (identity_claim, framework) pair;
  further submissions receive 429.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auth_context import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum number of unresolved pending requests allowed from the same
# identity_claim + framework before new submissions are rate-limited.
_PENDING_CAP = 5


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateAuthRequest(BaseModel):
    identity_claim: str
    framework: str
    requested_scopes: list[str]
    requested_skills: Optional[list[str]] = None
    reason: str = ""
    duration_secs: Optional[int] = None
    project_id: Optional[str] = None


class ApproveBody(BaseModel):
    granted_scopes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_auth_requests_store(request: Request):
    store = getattr(request.app.state, "auth_requests", None)
    if store is None:
        raise RuntimeError("auth_requests store not on app.state")
    return store


def _get_grants_store(request: Request):
    store = getattr(request.app.state, "agent_grants", None)
    if store is None:
        raise RuntimeError("agent_grants store not on app.state")
    return store


def _get_registry_store(request: Request):
    store = getattr(request.app.state, "agent_registry", None)
    if store is None:
        raise RuntimeError("agent_registry store not on app.state")
    return store


def _get_keypair(request: Request) -> tuple[bytes, bytes]:
    kp = getattr(request.app.state, "agent_registry_keypair", None)
    if kp is None:
        raise RuntimeError("agent_registry_keypair not on app.state")
    return kp


def _get_relationships(request: Request):
    rel = getattr(request.app.state, "relationships", None)
    if rel is None:
        raise RuntimeError("relationships manager not on app.state")
    return rel


# ---------------------------------------------------------------------------
# Routes — public (EXEMPT)
# ---------------------------------------------------------------------------

@router.post("/api/agents/auth-requests")
async def create_auth_request(request: Request, body: CreateAuthRequest):
    """Submit an access request from an external agent.

    No authentication required — the agent has no credentials yet.
    Returns {request_id, status: 'pending'}.
    """
    store = _get_auth_requests_store(request)

    # Abuse cap: reject if too many pending requests from the same identity.
    pending_count = await store.count_pending_for(
        body.identity_claim, body.framework
    )
    if pending_count >= _PENDING_CAP:
        raise HTTPException(
            status_code=429,
            detail=(
                f"too many pending requests from identity {body.identity_claim!r} "
                f"({pending_count} pending; resolve existing requests first)"
            ),
        )

    record = await store.create(
        identity_claim=body.identity_claim,
        framework=body.framework,
        requested_scopes=body.requested_scopes,
        requested_skills=body.requested_skills,
        reason=body.reason,
        duration_secs=body.duration_secs,
        project_id=body.project_id,
    )
    return {"request_id": record["id"], "status": "pending"}


@router.get("/api/agents/auth-requests/{request_id}")
async def get_auth_request_status(request: Request, request_id: str):
    """Poll the status of a consent request.

    No authentication required — the opaque request_id acts as a capability.
    Returns {status} on pending/refused, and additionally {canonical_id, token}
    once the request is accepted.
    """
    store = _get_auth_requests_store(request)
    record = await store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")

    result: dict = {"status": record["status"]}
    if record["status"] == "accepted":
        result["canonical_id"] = record["canonical_id"]
        result["token"] = record["token"]
    return result


# ---------------------------------------------------------------------------
# Routes — authenticated (admin only)
# ---------------------------------------------------------------------------

@router.post("/api/agents/auth-requests/{request_id}/approve")
async def approve_auth_request(
    request: Request,
    request_id: str,
    body: ApproveBody,
    user: CurrentUser = Depends(current_user),
):
    """Approve a pending consent request and mint an agent identity.

    Flow:
    1. Load the pending request (404/409 guard).
    2. Register the agent in the registry → canonical_id.
    3. Issue a signed EdDSA JWT token.
    4. Write per-scope grants (RelationshipManager edge + AgentGrantsStore).
    5. Atomically mark the request accepted with canonical_id + token.

    Returns {status: 'accepted', canonical_id}.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    auth_store = _get_auth_requests_store(request)
    record = await auth_store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")
    if record["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"request is already {record['status']!r}; cannot approve",
        )

    registry = _get_registry_store(request)
    private_pem, _public_pem = _get_keypair(request)
    grants_store = _get_grants_store(request)
    rel_mgr = _get_relationships(request)

    # Mint canonical identity in the registry.
    reg_record = await registry.register(
        framework=record["framework"],
        display_name=record["identity_claim"],
        user_id=user.user_id,
        origin="external-selfjoin",
        handle="",
    )
    canonical_id = reg_record["canonical_id"]

    # Issue the identity token.
    token = mint_registry_token(
        canonical_id,
        private_pem,
        user_id=user.user_id,
        framework=record["framework"],
    )

    # Record grants for each approved scope.
    for scope in body.granted_scopes:
        await grants_store.add_grant(canonical_id, scope, tier="once")
        # Also write a RelationshipManager permission edge so the existing
        # permission-check path (can_communicate etc.) is aware of the agent.
        await rel_mgr.set_permission(canonical_id, "taos-instance", scope)

    # Atomically commit the decision.
    result = await auth_store.set_decision(
        request_id,
        "accepted",
        canonical_id=canonical_id,
        token=token,
        granted_scopes=body.granted_scopes,
        decided_by=user.user_id,
    )
    if result is None:
        # Another concurrent approve beat us — 409.
        raise HTTPException(
            status_code=409,
            detail="request was decided concurrently; check current status",
        )

    return {"status": "accepted", "canonical_id": canonical_id}


@router.post("/api/agents/auth-requests/{request_id}/deny")
async def deny_auth_request(
    request: Request,
    request_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Deny a pending consent request (admin only).

    Denial is recorded as 'refused'.  Per the spec it is reversible in
    Phase 2 — for now the request simply stays in the DB with status refused.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    store = _get_auth_requests_store(request)
    record = await store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")
    if record["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"request is already {record['status']!r}; cannot deny",
        )

    result = await store.set_decision(
        request_id,
        "refused",
        decided_by=user.user_id,
    )
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="request was decided concurrently; check current status",
        )

    return {"status": "refused"}


@router.get("/api/agents/auth-requests")
async def list_auth_requests(
    request: Request,
    status: Optional[str] = "pending",
    user: CurrentUser = Depends(current_user),
):
    """List pending auth requests (admin only).

    This is the feed the desktop notification / Permissions app reads.
    The ?status= filter defaults to 'pending'; pass status=all for every record
    (not yet implemented — Phase 1 only needs the pending feed).
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    store = _get_auth_requests_store(request)
    pending = await store.list_pending()
    return {"requests": pending}
