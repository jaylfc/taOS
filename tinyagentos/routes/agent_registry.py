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

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.agent_registry_store import mint_registry_token
from tinyagentos.agent_token_auth import check_agent_scope
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


# Scopes an operator may grant when minting an internal agent. Mirrors the
# consent-flow VALID_SCOPES; the mint route must not be a back door to invent
# arbitrary scopes that the rest of the system does not understand.
_ALLOWED_SCOPES = frozenset({
    "memory_read", "memory_write",
    "a2a_send", "a2a_receive",
    "files_read", "files_write",
    "tools_execute", "registry_feeds_read",
})


class MintInternalRequest(BaseModel):
    """Body for minting an internal driver-agent identity (admin only)."""

    handle: str
    slug: str
    scopes: list[str] = []
    project_id: Optional[str] = None
    # When the handle is already owned by a non-internal agent (e.g. a driver
    # agent that earlier self-joined via the consent flow), default-deny: the
    # admin must set adopt=true to vouch for that existing identity and grant it
    # driver scopes + a token. Guards against an impostor that grabbed a handle.
    adopt: bool = False

    @field_validator("handle", "slug")
    @classmethod
    def _require_non_empty(cls, v: str) -> str:
        val = (v or "").strip()
        if not val:
            raise ValueError("must not be empty")
        return val

    @field_validator("scopes")
    @classmethod
    def _known_scopes(cls, v: list[str]) -> list[str]:
        bad = [s for s in v if s not in _ALLOWED_SCOPES]
        if bad:
            raise ValueError(f"unknown scope(s): {sorted(bad)}")
        return v


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


_FEED_SCOPE = "registry_feeds_read"

# Serializes internal-identity minting so a concurrent check-then-register for
# the same handle cannot create two rows. The controller is a single process, so
# an in-process lock is sufficient -- and is preferable to a DB-wide unique
# index on handle, which would make unrelated active-handle writes (set_status,
# update) raise on collision and 500.
_mint_lock = asyncio.Lock()

# Origin marker for the built-in driver agents seeded by the operator.  register()
# treats any origin other than "external-selfjoin" as immediately active, so these
# come up active without a consent round-trip.
_INTERNAL_ORIGIN = "taos-internal"

# The four internal driver agents and the scopes they need to read/write the
# coordination bus.  seed-internal mints all four idempotently by handle.
_INTERNAL_AGENT_SCOPES = ["a2a_send", "a2a_receive"]
_INTERNAL_AGENTS = (
    {"handle": "@taOS-dev", "slug": "taos-dev"},
    {"handle": "@taOS-website-dev", "slug": "taos-website-dev"},
    {"handle": "@taOSmd-dev", "slug": "taosmd-dev"},
    {"handle": "@Hermes", "slug": "hermes"},
)


async def _check_feed_token(request: Request) -> Optional[str]:
    """Return the canonical_id from a valid Bearer token that holds an active
    ``registry_feeds_read`` grant, or raise an HTTPException.

    Thin wrapper over the shared ``check_agent_scope`` helper so the feed routes
    and the A2A bus routes use one verify path with identical 401/403 semantics.
    Returns None when no Authorization header is present (caller falls through
    to the standard admin session check).
    """
    return await check_agent_scope(request, _FEED_SCOPE)


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


async def _mint_internal_identity(
    request: Request,
    user: CurrentUser,
    *,
    handle: str,
    slug: str,
    scopes: list[str],
    project_id: Optional[str] = None,
    adopt: bool = False,
) -> dict:
    """Register-or-reuse a driver-agent identity by handle, ensure its grants,
    and mint a registry JWT.  Idempotent: a second call with the same handle
    reuses the existing active canonical_id and re-asserts the (idempotent)
    grants.  When the handle is already owned by a NON-internal agent (e.g. a
    driver that self-joined via the consent flow), default-deny with 409 unless
    ``adopt`` is set -- then the admin is explicitly vouching for that identity.
    """
    store = _get_store(request)
    grants_store = _get_grants_store(request)
    private_pem, _public_pem = _get_keypair(request)

    # The lock makes the check-then-register atomic: a concurrent mint of the
    # same handle waits, then sees the row the first one created and reuses it.
    async with _mint_lock:
        existing = await store.get_by_handle(handle)
        if existing is not None:
            # A non-internal owner is only reused when the admin adopts it: this
            # blocks an impostor that grabbed "@Hermes" from silently receiving
            # driver scopes + a token, while still letting the operator vouch for
            # a legitimately pre-existing driver (its origin is left untouched).
            adopted = existing.get("origin") != _INTERNAL_ORIGIN
            if adopted and not adopt:
                raise HTTPException(
                    status_code=409,
                    detail=("handle is already owned by a non-internal agent; "
                            "pass adopt=true to vouch for it and grant driver scopes"),
                )
            record = existing
            created = False
        else:
            adopted = False
            record = await store.register(
                framework=_INTERNAL_ORIGIN,
                display_name=slug,
                user_id=user.user_id,
                origin=_INTERNAL_ORIGIN,
                handle=handle,
                capabilities=[],
            )
            created = True

    canonical_id = record["canonical_id"]
    for scope in scopes:
        await grants_store.add_grant(canonical_id, scope, project_id=project_id)

    token = mint_registry_token(
        canonical_id,
        private_pem,
        user_id=user.user_id,
        framework=record.get("framework", ""),
        project_id=project_id,
    )

    # Minting a driver token is a privileged, trust-changing action -- leave a
    # forensic trail. Adopt is the most sensitive (it elevates a possibly
    # untrusted-origin identity), so it gets its own action name; create/reuse
    # are logged too so every token issuance is traceable to an actor.
    status_now = record.get("status", "")
    await _audit_governance(
        request,
        action="adopt" if adopted else ("mint-internal-create" if created else "mint-internal-reuse"),
        canonical_id=canonical_id,
        actor_user_id=user.user_id,
        before_status=status_now,
        after_status=status_now,
    )
    return {
        "handle": handle,
        "canonical_id": canonical_id,
        "created": created,
        "adopted": adopted,
        "scopes": scopes,
        "token": token,
    }


@router.post("/api/agents/registry/mint-internal")
async def mint_internal_agent(
    request: Request,
    body: MintInternalRequest,
    user: CurrentUser = Depends(current_user),
):
    """Mint (or reuse) an internal driver-agent identity and return its token.

    Admin only.  Idempotent by handle: re-running reuses the existing active
    canonical_id instead of creating a duplicate row, and re-asserts the
    requested scope grants (add_grant is idempotent).  The token is returned so
    the operator can store it on the agent host -- it is never written to disk
    here.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    return await _mint_internal_identity(
        request,
        user,
        handle=body.handle,
        slug=body.slug,
        scopes=body.scopes,
        project_id=body.project_id,
        adopt=body.adopt,
    )


@router.post("/api/agents/registry/seed-internal")
async def seed_internal_agents(
    request: Request,
    user: CurrentUser = Depends(current_user),
    adopt: bool = False,
):
    """Idempotently mint the four internal driver agents and return their tokens.

    Admin only.  Each of @taOS-dev, @taOS-website-dev, @taOSmd-dev, @Hermes is
    minted with the a2a_send + a2a_receive scopes.  Re-running creates no
    duplicate rows.  ``adopt=true`` (query param) vouches for any of those
    handles that already exist under a non-internal origin (e.g. a driver that
    self-joined earlier) instead of 409ing.  Response: {"seeded": [{handle,
    canonical_id, created, adopted, scopes, token}, ...]}.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    seeded = []
    for spec in _INTERNAL_AGENTS:
        seeded.append(
            await _mint_internal_identity(
                request,
                user,
                handle=spec["handle"],
                slug=spec["slug"],
                scopes=list(_INTERNAL_AGENT_SCOPES),
                adopt=adopt,
            )
        )
    return {"seeded": seeded}


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
async def list_revoked_entries(request: Request):
    """Return the global revocation feed: [{canonical_id, revoked_at}, ...].

    Accessible to admin sessions/local-token OR a registry JWT whose
    canonical_id holds an active ``registry_feeds_read`` grant.  The grant is
    issued through the normal consent-flow path; no separate minting machinery
    exists.  JWT revocation is handled by suspending the agent or expiring the
    grant -- the token itself carries no exp claim.
    """
    # Admin (session or local token) wins before any JWT verification, so an
    # admin-equivalent Bearer local token is never mis-verified as a registry
    # JWT and rejected.
    if not getattr(request.state, "is_admin", False):
        feed_caller = await _check_feed_token(request)
        if feed_caller is None:
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
async def list_active_grants(request: Request, canonical_id: Optional[str] = None):
    """Return the active grant feed for A2A bus enforcement.

    Response: {"grants": [{canonical_id, scope, tier, project_id, granted_at, expires_at}, ...]}

    Accessible to admin sessions/local-token OR a registry JWT whose
    canonical_id holds an active ``registry_feeds_read`` grant.  The grant is
    issued through the normal consent-flow path; no separate minting machinery
    exists.  JWT revocation is handled by suspending the agent or expiring the
    grant -- the token itself carries no exp claim.

    @taOSmd polls this on interval to keep its local cache current.
    Grants are active if expires_at IS NULL or expires_at > now (Phase 1: all
    grants are non-expiring, so the full list is always returned).

    Optional ``?canonical_id=`` filter narrows to a single agent.
    """
    # Admin (session or local token) wins before any JWT verification, so an
    # admin-equivalent Bearer local token is never mis-verified as a registry
    # JWT and rejected.
    is_admin = getattr(request.state, "is_admin", False)
    if not is_admin:
        feed_caller = await _check_feed_token(request)
        if feed_caller is None:
            raise HTTPException(status_code=403, detail="forbidden")
    grants_store = _get_grants_store(request)
    if canonical_id:
        grants = await grants_store.list_grants(canonical_id)
        if not is_admin:
            # Feed-scoped callers see the same view as the unfiltered feed:
            # active grants only, no expired history.
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            grants = [
                g for g in grants
                if not g.get("expires_at") or g["expires_at"] > now
            ]
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
    old_name = record.get("display_name") or ""
    updated = await store.update(
        canonical_id,
        display_name=body.display_name,
        handle=body.handle,
        role=body.role,
        capabilities=body.capabilities,
    )
    if updated is None:
        # The entry was revoked/removed between the get and the update; do not
        # emit a rename notification for a row that no longer exists.
        return JSONResponse({"error": "not found"}, status_code=404)
    new_name = body.display_name
    if new_name is not None and new_name != old_name:
        notif_store = getattr(request.app.state, "notifications", None)
        if notif_store is not None:
            # Best effort: the rename has already persisted, so a notification
            # failure must not turn a successful update into a 500.
            try:
                await notif_store.add(
                    title="Agent renamed",
                    message=f"Agent {canonical_id} renamed from {old_name!r} to {new_name!r}",
                    level="info",
                    source="agent_registry",
                )
            except Exception:
                logger.warning("rename notification failed for %s", canonical_id, exc_info=True)
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
