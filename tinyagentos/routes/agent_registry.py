from __future__ import annotations

"""Routes for the Agent Registry (SP-A, taOS side).

POST /api/agents/registry/register  — register an agent, mint canonical_id, issue token
GET  /api/agents/registry/pubkey    — public key for token verification (bus side)
GET  /api/agents/registry           — list all registry entries
GET  /api/agents/registry/revoked   — revocation feed (canonical_id + revoked_at) the bus polls
GET  /api/agents/registry/{id}      — read a single entry
DELETE /api/agents/registry/{id}    — revoke an entry

The pubkey endpoint is intentionally unauthenticated so the A2A bus (taOSmd)
can fetch it on its own schedule without a session cookie.
"""

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_registry_store import mint_registry_token, verify_registry_token

router = APIRouter()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    framework: str
    display_name: Optional[str] = ""
    user_id: Optional[str] = ""
    origin: Optional[str] = "taos-deployed"
    handle: Optional[str] = ""
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


def _get_keypair(request: Request) -> tuple[bytes, bytes]:
    kp = getattr(request.app.state, "agent_registry_keypair", None)
    if kp is None:
        raise RuntimeError("agent_registry_keypair not on app.state")
    return kp


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/agents/registry/register")
async def register_agent(request: Request, body: RegisterRequest):
    """Register an agent and issue a signed identity token."""
    store = _get_store(request)
    private_pem, _public_pem = _get_keypair(request)

    record = await store.register(
        framework=body.framework,
        display_name=body.display_name or "",
        user_id=body.user_id or "",
        origin=body.origin or "taos-deployed",
        handle=body.handle or "",
        role=body.role,
        capabilities=body.capabilities or [],
    )

    token = mint_registry_token(
        record["canonical_id"],
        private_pem,
        user_id=record.get("user_id", ""),
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

    This endpoint is intentionally open (no auth required) so the A2A bus
    can fetch the key to verify tokens independently.
    """
    _private_pem, public_pem = _get_keypair(request)
    return {
        "alg": "EdDSA",
        "format": "PEM",
        "public_key": public_pem.decode("ascii"),
    }


@router.get("/api/agents/registry")
async def list_registry(request: Request):
    """List all registry entries."""
    store = _get_store(request)
    records = await store.list_all()
    return records


@router.get("/api/agents/registry/revoked")
async def list_revoked(request: Request):
    """Lightweight revocation feed for the A2A bus to poll.

    Returns ``{"revoked": [{canonical_id, revoked_at}, ...]}``. The bus caches
    this and rejects any token whose ``sub`` (canonical_id) is in the set.
    Declared before the ``/{canonical_id}`` route so "revoked" is not matched
    as a canonical_id.
    """
    store = _get_store(request)
    revoked = await store.list_revoked()
    return {"revoked": revoked}


@router.get("/api/agents/registry/{canonical_id}")
async def get_registry_entry(request: Request, canonical_id: str):
    """Fetch a single registry entry by canonical_id."""
    store = _get_store(request)
    record = await store.get(canonical_id)
    if record is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return record


@router.delete("/api/agents/registry/{canonical_id}")
async def revoke_registry_entry(request: Request, canonical_id: str):
    """Revoke a registry entry (sets revoked_at, does not delete)."""
    store = _get_store(request)
    record = await store.revoke(canonical_id)
    if record is None:
        return JSONResponse({"error": "not found or already revoked"}, status_code=404)
    return {"status": "revoked", "canonical_id": canonical_id, "revoked_at": record.get("revoked_at")}
