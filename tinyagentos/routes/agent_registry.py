from __future__ import annotations

"""Routes for the Agent Registry (SP-A, taOS side).

POST   /api/agents/registry/register    — register an agent, mint canonical_id, issue token
GET    /api/agents/registry/pubkey      — public key for token verification (exempt, no auth)
GET    /api/agents/registry/revoked     — global revocation feed (admin/local-token only)
GET    /api/agents/registry             — list registry entries (admin: all; member: own)
GET    /api/agents/registry/{id}        — read a single entry (owner or admin; else 404)
DELETE /api/agents/registry/{id}        — revoke an entry (owner or admin)

Route ordering matters: /pubkey and /revoked are declared before /{canonical_id} so
the literal strings are not captured as a canonical_id path parameter.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin

router = APIRouter()

_ALLOWED_ORIGINS = {"taos-deployed", "external-selfjoin"}


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_store(request: Request):
    store = getattr(request.app.state, "agent_registry", None)
    if store is None:
        raise RuntimeError("agent_registry store not on app.state")
    return store


def _get_keypair(request: Request) -> tuple[bytes, bytes]:
    kp = getattr(request.app.state, "agent_registry_keypair", None)
    if kp is None:
        raise RuntimeError("agent_registry_keypair not on app.state")
    return kp


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


@router.get("/api/agents/registry")
async def list_registry(
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """List registry entries. Admins see all; members see only their own."""
    store = _get_store(request)
    if user.is_admin:
        return await store.list_all()
    return await store.list_for_user(user.user_id)


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
    revoked = await store.revoke(canonical_id)
    return {"status": "revoked", "canonical_id": canonical_id, "revoked_at": revoked.get("revoked_at")}
