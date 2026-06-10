from __future__ import annotations

"""Routes for the Agent Registry (SP-A, taOS side).

POST   /api/agents/registry/register         — register an agent, mint canonical_id, issue token
GET    /api/agents/registry/pubkey           — public key for token verification (exempt, no auth)
GET    /api/agents/registry/revoked          — global revocation feed (admin/local-token only)
GET    /api/agents/registry/inactive         — all non-active entries for the bus (admin only)
GET    /api/agents/registry/grants           — active grant feed for @taOSmd enforcement (admin only)
GET    /api/agents/registry                  — list registry entries (admin: all; member: own)
GET    /api/agents/registry/{id}             — read a single entry (owner or admin; else 404)
PATCH  /api/agents/registry/{id}             — update mutable fields (owner or admin)
DELETE /api/agents/registry/{id}             — revoke an entry (owner or admin)
POST   /api/agents/registry/{id}/approve     — lifecycle: pending → active (admin only)
POST   /api/agents/registry/{id}/reject      — lifecycle: pending → rejected (admin only)
POST   /api/agents/registry/{id}/suspend     — lifecycle: active → suspended (admin only)
POST   /api/agents/registry/{id}/reactivate  — lifecycle: suspended → active (admin only)

Route ordering matters: /pubkey, /revoked, and /inactive are declared before
/{canonical_id} so the literal strings are not captured as a path parameter.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_ORIGINS = {"taos-deployed", "external-selfjoin"}

# Dedicated trace slug for governance audit events.
_GOVERNANCE_SLUG = "taos-governance"


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    framework: str
    display_name: Optional[str] = ""
    origin: Optional[str] = "taos-deployed"
    handle: Optional[str] = ""
    role: Optional[str] = None
    capabilities: Optional[list[str]] = None

    @field_validator("origin")
    @classmethod
    def _validate_origin(cls, v: Optional[str]) -> Optional[str]:
        val = v or "taos-deployed"
        if val not in _ALLOWED_ORIGINS:
            raise ValueError(f"origin must be one of {sorted(_ALLOWED_ORIGINS)}")
        return val


class PatchRegistryRequest(BaseModel):
    display_name: Optional[str] = None
    handle: Optional[str] = None
    role: Optional[str] = None
    capabilities: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_store(request: Request):
    store = getattr(request.app.state, "agent_registry", None)
    if store is None:
        raise RuntimeError("agent_registry store not on app.state")
    return store


def _get_grants_store(request: Request):
    store = getattr(request.app.state, "agent_grants", None)
    if store is None:
        raise RuntimeError("agent_grants store not on app.state")
    return store


def _get_keypair(request: Request) -> tuple[bytes, bytes]:
    kp = getattr(request.app.state, "agent_registry_keypair", None)
    if kp is None:
        raise RuntimeError("agent_registry_keypair not on app.state")
    return kp


async def _audit_governance(
    request: Request,
    *,
    action: str,
    canonical_id: str,
    actor_user_id: str,
    before_status: str,
    after_status: str,
) -> None:
    """Write a governance audit event to the trace store (best-effort, non-fatal)."""
    try:
        trace_registry = getattr(request.app.state, "trace_registry", None)
        if trace_registry is None:
            return
        ts = await trace_registry.get(_GOVERNANCE_SLUG)
        await ts.record(
            "governance",
            payload={
                "action": action,
                "canonical_id": canonical_id,
                "actor_user_id": actor_user_id,
                "before_status": before_status,
                "after_status": after_status,
            },
        )
    except Exception:
        logger.exception("governance audit write failed (non-fatal)")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/agents/registry/register")
async def register_agent(
    request: Request,
    body: RegisterRequest,
    user: CurrentUser = Depends(current_user),
):
    """Register an agent and issue a signed identity token.

    The minted token's user_id is the authenticated caller's id — not
    a value from the request body, so identity cannot be spoofed.
    """
    store = _get_store(request)
    private_pem, _public_pem = _get_keypair(request)

    record = await store.register(
        framework=body.framework,
        display_name=body.display_name or "",
        user_id=user.user_id,
        origin=body.origin or "taos-deployed",
        handle=body.handle or "",
        role=body.role,
        capabilities=body.capabilities or [],
    )

    token = mint_registry_token(
        record["canonical_id"],
        private_pem,
        user_id=user.user_id,
        framework=record.get("framework", ""),
    )
    return {
        "canonical_id": record["canonical_id"],
        "token": token,
        "record": record,
    }


@router.get("/api/agents/registry/pubkey")
async def get_pubkey(request: Request):
    """Return the registry's Ed25519 public key in PEM format.

    This endpoint is exempt from authentication (listed in EXEMPT_PATHS) so
    the A2A bus (taOSmd) can fetch the key on its own schedule without a
    session cookie or local token.
    """
    _private_pem, public_pem = _get_keypair(request)
    return {
        "alg": "EdDSA",
        "format": "PEM",
        "public_key": public_pem.decode("ascii"),
    }


@router.get("/api/agents/registry/revoked")
async def list_revoked_entries(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Return the global revocation feed: [{canonical_id, revoked_at}, ...].

    Admin or local-token only — this is the set the A2A bus needs to check
    whether a token has been revoked.  Members do not have access.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    store = _get_store(request)
    return {"revoked": await store.list_revoked()}


@router.get("/api/agents/registry/inactive")
async def list_inactive_entries(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Return all non-active registry entries for bus enforcement.

    Response: {"inactive": [{canonical_id, status}, ...]}

    Admin only — covers pending/suspended/rejected/revoked.
    The bus polls this to reject any canonical_id present.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    store = _get_store(request)
    return {"inactive": await store.list_inactive()}


@router.get("/api/agents/registry/grants")
async def list_active_grants(
    request: Request,
    canonical_id: Optional[str] = None,
    user: CurrentUser = Depends(current_user),
):
    """Return the active grant feed for A2A bus enforcement.

    Response: {"grants": [{canonical_id, scope, tier, project_id, granted_at, expires_at}, ...]}

    Admin only — @taOSmd polls this on interval to keep its local cache current.
    Grants are active if expires_at IS NULL or expires_at > now (Phase 1: all
    grants are non-expiring, so the full list is always returned).

    Optional ``?canonical_id=`` filter narrows to a single agent.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    grants_store = _get_grants_store(request)
    if canonical_id:
        grants = await grants_store.list_grants(canonical_id)
    else:
        grants = await grants_store.list_active_grants()
    return {"grants": grants}


@router.get("/api/agents/registry")
async def list_registry(
    request: Request,
    status: Optional[str] = None,
    user: CurrentUser = Depends(current_user),
):
    """List registry entries.

    Admins see all matching entries; members see only their own.
    Optional ``?status=<value>`` filter.
    """
    store = _get_store(request)
    if user.is_admin:
        return await store.list_all(status=status)
    return await store.list_for_user(user.user_id, status=status)


@router.get("/api/agents/registry/{canonical_id}")
async def get_registry_entry(
    request: Request,
    canonical_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Fetch a single registry entry by canonical_id.

    Returns 404 for unknown entries and for entries the caller does not own
    (existence-hiding — avoids disclosing whether an id exists to non-owners).
    """
    store = _get_store(request)
    record = await store.get(canonical_id)
    if record is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not user.is_admin and user.user_id != record["user_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    return record


@router.patch("/api/agents/registry/{canonical_id}")
async def patch_registry_entry(
    request: Request,
    canonical_id: str,
    body: PatchRegistryRequest,
    user: CurrentUser = Depends(current_user),
):
    """Update mutable metadata fields on a registry entry.

    Allowed fields: display_name, handle, role, capabilities.
    Status, framework, user_id, and timestamps are immutable.
    Only the owning user or an admin may update an entry.
    """
    store = _get_store(request)
    record = await store.get(canonical_id)
    if record is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    require_owner_or_admin(user, record["user_id"])
    updated = await store.update(
        canonical_id,
        display_name=body.display_name,
        handle=body.handle,
        role=body.role,
        capabilities=body.capabilities,
    )
    return updated


@router.delete("/api/agents/registry/{canonical_id}")
async def revoke_registry_entry(
    request: Request,
    canonical_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Revoke a registry entry (sets revoked_at, does not delete).

    Only the owning user or an admin may revoke an entry.
    """
    store = _get_store(request)
    record = await store.get(canonical_id)
    if record is None:
        return JSONResponse({"error": "not found or already revoked"}, status_code=404)
    require_owner_or_admin(user, record["user_id"])
    before_status = record.get("status") or "active"
    revoked = await store.revoke(canonical_id)
    await _audit_governance(
        request,
        action="revoke",
        canonical_id=canonical_id,
        actor_user_id=user.user_id,
        before_status=before_status,
        after_status="revoked",
    )
    return {"status": "revoked", "canonical_id": canonical_id, "revoked_at": revoked.get("revoked_at")}


# ---------------------------------------------------------------------------
# Lifecycle transition routes (admin only)
# ---------------------------------------------------------------------------

async def _transition(
    request: Request,
    canonical_id: str,
    action: str,
    new_status: str,
    user: CurrentUser,
):
    """Shared handler for approve / reject / suspend / reactivate."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    store = _get_store(request)
    record = await store.get(canonical_id)
    if record is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    before_status = record.get("status") or "active"
    try:
        updated = await store.set_status(canonical_id, new_status, actor=user.user_id)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)

    await _audit_governance(
        request,
        action=action,
        canonical_id=canonical_id,
        actor_user_id=user.user_id,
        before_status=before_status,
        after_status=new_status,
    )
    return updated


@router.post("/api/agents/registry/{canonical_id}/approve")
async def approve_agent(
    request: Request,
    canonical_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Approve a pending agent (pending → active). Admin only."""
    return await _transition(request, canonical_id, "approve", "active", user)


@router.post("/api/agents/registry/{canonical_id}/reject")
async def reject_agent(
    request: Request,
    canonical_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Reject a pending agent (pending → rejected). Admin only."""
    return await _transition(request, canonical_id, "reject", "rejected", user)


@router.post("/api/agents/registry/{canonical_id}/suspend")
async def suspend_agent(
    request: Request,
    canonical_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Suspend an active agent (active → suspended). Admin only."""
    return await _transition(request, canonical_id, "suspend", "suspended", user)


@router.post("/api/agents/registry/{canonical_id}/reactivate")
async def reactivate_agent(
    request: Request,
    canonical_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Reactivate a suspended agent (suspended → active). Admin only."""
    return await _transition(request, canonical_id, "reactivate", "active", user)
