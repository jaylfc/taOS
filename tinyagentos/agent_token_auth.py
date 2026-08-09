from __future__ import annotations

"""Shared registry-JWT scope check for agent-authenticated routes.

A registered agent authenticates to taOS with its OWN Ed25519 registry JWT
(minted by ``mint_registry_token`` / verified by ``verify_registry_token``),
never the owner password.  ``check_agent_scope`` is the single verify path used
by every route that accepts an agent token:

  - routes/agent_registry.py  -> required_scope "registry_feeds_read"
  - routes/a2a_bus.py         -> required_scope "a2a_receive"

Semantics (fail closed):
  - No Authorization Bearer header -> return None (the caller decides what to do,
    typically reject; the admin session/local-token path is handled before this).
  - Bearer present but malformed / bad signature / missing sub -> 401.
  - Valid signature but the agent is not active in the registry, the sub is
    unknown, or the required scope grant is missing/expired -> 403.

The registry JWT itself carries no exp claim; per-identity token rotation is
achieved by bumping ``token_min_iat`` on the registry record — any token whose
``iat`` is strictly less than the cutoff is rejected as superseded.  Per-agent
revocation (suspending the agent or expiring the grant) remains available.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request

from tinyagentos.agent_registry_store import verify_registry_token


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


def _grant_unexpired(expires_at, now: datetime) -> bool:
    """True if a grant's expiry is in the future (or it never expires).

    Parses ``expires_at`` to a datetime instead of comparing ISO strings
    lexicographically: a string compare is only correct when every stored value
    uses the exact same UTC offset + precision, and silently mis-ranks a naive
    or differently-formatted timestamp -- which could read an expired grant as
    live (access after revocation). Fail closed: an unparseable value is treated
    as expired. A naive timestamp is assumed UTC.
    """
    if expires_at is None:
        return True
    try:
        exp = datetime.fromisoformat(str(expires_at))
    except (ValueError, TypeError):
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp > now


def _get_keypair(request: Request) -> tuple[bytes, bytes]:
    kp = getattr(request.app.state, "agent_registry_keypair", None)
    if kp is None:
        raise RuntimeError("agent_registry_keypair not on app.state")
    return kp


# Detail string for a token whose project_id claim does not match the requested
# project.  Shared with the project task routes so they can recognise this exact
# rejection and collapse it into an existence-hiding 404 (a token bound to
# another project must be indistinguishable from a non-owner session).
PROJECT_SCOPE_MISMATCH_DETAIL = "token not scoped to this project"


async def _verify_agent_scope(
    request: Request, required_scope: str
) -> Optional[tuple[str, dict]]:
    """Shared verifier for the agent-token checks.

    Return ``(canonical_id, payload)`` for a valid Bearer registry JWT that holds
    an active *required_scope* grant, or ``None`` when no Authorization header is
    present.  Raises the same 401/403 documented on ``check_agent_scope``.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None

    raw_token = auth_header[7:].strip()

    # Verify the EdDSA signature using the registry public key.
    _private_pem, public_pem = _get_keypair(request)
    try:
        payload = verify_registry_token(raw_token, public_pem)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid or malformed registry token")

    canonical_id: str = payload.get("sub", "")
    if not canonical_id:
        raise HTTPException(status_code=401, detail="token missing sub claim")

    # Agent must be active in the registry.
    registry = _get_store(request)
    record = await registry.get(canonical_id)
    if record is None or record.get("status") != "active":
        raise HTTPException(status_code=403, detail="agent is not active in the registry")

    # Reject tokens issued before the identity's token_min_iat cutoff (rotation).
    token_min_iat = record.get("token_min_iat") or 0
    token_iat = payload.get("iat") or 0
    if token_iat < token_min_iat:
        raise HTTPException(status_code=401, detail="token superseded")

    # Must hold an active grant for the required scope.
    grants_store = _get_grants_store(request)
    grants = await grants_store.list_grants(canonical_id)

    now = datetime.now(timezone.utc)
    has_scope = any(
        g["scope"] == required_scope and _grant_unexpired(g.get("expires_at"), now)
        for g in grants
    )
    if not has_scope:
        raise HTTPException(
            status_code=403,
            detail=f"token does not hold an active {required_scope!r} grant",
        )

    # For decisions_write, the route-level guard (check_agent_scope_for_project)
    # verifies the agent's GRANT project_id matches the decision's project_id.
    # The token's own project_id claim is advisory only and is not checked
    # (taOS #1862) -- see check_agent_scope_for_project below.
    return canonical_id, payload


async def check_agent_scope(request: Request, required_scope: str) -> Optional[str]:
    """Return the canonical_id from a valid Bearer registry JWT that holds an
    active *required_scope* grant, or raise an HTTPException.

    Returns None when no Authorization header is present (the caller falls
    through to its own admin/session handling).

    Raises:
      401 -- Authorization header present but the token is malformed, has a bad
             signature, or is missing the sub claim.
      403 -- Token is valid but the agent is not active in the registry, the
             sub is unknown, or the grant is missing/expired.
    """
    result = await _verify_agent_scope(request, required_scope)
    if result is None:
        return None
    canonical_id, _payload = result
    return canonical_id


async def check_agent_identity(request: Request) -> Optional[str]:
    """Return the canonical_id from a valid Bearer registry JWT for an ACTIVE
    agent, without requiring any scope grant.

    This proves only *who* the caller is, not *what* it may do — the caller is
    responsible for the authorization decision (e.g. only allowing an agent to
    act on its OWN canonical_id).  It is used by the scope-request create flow,
    where an already-registered agent asks for MORE scopes: it must not need a
    scope it does not yet hold in order to request one.

    Returns None when no Authorization header is present (the caller falls
    through to its own admin/session handling).

    Raises:
      401 -- Authorization header present but the token is malformed, has a bad
             signature, or is missing the sub claim.
      403 -- Token is valid but the agent is not active in the registry.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None

    raw_token = auth_header[7:].strip()

    _private_pem, public_pem = _get_keypair(request)
    try:
        payload = verify_registry_token(raw_token, public_pem)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid or malformed registry token")

    canonical_id: str = payload.get("sub", "")
    if not canonical_id:
        raise HTTPException(status_code=401, detail="token missing sub claim")

    registry = _get_store(request)
    record = await registry.get(canonical_id)
    if record is None or record.get("status") != "active":
        raise HTTPException(status_code=403, detail="agent is not active in the registry")

    return canonical_id


async def check_agent_scope_for_project(
    request: Request, required_scope: str, project_id: str
) -> Optional[str]:
    """Project-scoped sibling of ``check_agent_scope`` (least privilege).

    The token is the identity (project-agnostic); per-project GRANTS are the
    sole authorization.  This function verifies the same EdDSA JWT + active
    scope-grant chain as ``check_agent_scope`` and then authorizes ONLY when
    the agent holds an active grant for *required_scope* bound to *project_id*.

    NOTE (taOS #1862): the hard token-project pin was removed.  The registry
    JWT's ``project_id`` claim (set by ``mint_registry_token``) is now ADVISORY
    ONLY - it is not used to gate access.  An agent holding matching active
    grants can use ONE project-agnostic token to authorize any project it has
    been granted, and is rejected for any project it lacks a grant for.  See
    the grant-gated model in docs/agent-coordination.md.

    Returns None when no Authorization header is present.

    Raises:
      401/403 -- exactly as ``check_agent_scope`` (bad token / inactive agent /
                 missing scope grant).
      403 -- token is valid and active but holds no active *required_scope*
                  grant bound to *project_id* (grant-gated, not claim-gated).
    """
    result = await _verify_agent_scope(request, required_scope)
    if result is None:
        return None
    canonical_id, _payload = result
    # Grant-gated authorization: a project is reachable only if the agent holds
    # an active grant for the required scope bound to that project. The token's
    # project_id claim is advisory and is intentionally NOT checked here (taOS #1862).
    grants_store = _get_grants_store(request)
    now = datetime.now(timezone.utc)
    grants = await grants_store.list_grants(canonical_id)
    grant_ok = any(
        g["scope"] == required_scope
        and g.get("project_id") == project_id
        and _grant_unexpired(g.get("expires_at"), now)
        for g in grants
    )
    if not grant_ok:
        raise HTTPException(status_code=403, detail=PROJECT_SCOPE_MISMATCH_DETAIL)
    return canonical_id
