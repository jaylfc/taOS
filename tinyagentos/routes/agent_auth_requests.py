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

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aiosqlite import IntegrityError
from tinyagentos.agent_registry_store import _slugify, mint_registry_token
from tinyagentos.auth_context import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximum number of unresolved pending requests allowed from the same
# identity_claim + framework before new submissions are rate-limited.
_PENDING_CAP = 5

# Closed vocabulary of grantable scopes — surfaced to the user in the
# desktop consent actions (desktop/src/components/ConsentActions.tsx).
VALID_SCOPES = frozenset({
    "memory_read",
    "memory_write",
    "a2a_send",
    "a2a_receive",
    "files_read",
    "files_write",
    "tools_execute",
    "registry_feeds_read",
    # Least-privilege kanban access: task read + lifecycle + comments for the
    # agent's OWN project only (bound by the token's project_id claim). Does NOT
    # grant task create, member management, or project lifecycle.
    "project_tasks",
    # Canvas access: read and write on a specific project's canvas. Like
    # project_tasks, a project_id is required so the token is bound to the
    # operator-validated project rather than whatever the unauthenticated agent
    # named in the request.
    "canvas_read",
    "canvas_write",
})


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
    project_id: Optional[str] = None


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


async def _retire_request_notification(request: Request, request_id: str) -> None:
    """Archive the bell notification for a now-decided auth request so it leaves
    the active list. Best effort: never fails the decision."""
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is None:
        return
    try:
        await notifs.archive_by_source_ref("auth_requests", request_id)
    except Exception:
        pass


def _get_approve_lock(request: Request, request_id: str) -> asyncio.Lock:
    """Per-request-id lock preventing concurrent approve races."""
    locks = getattr(request.app.state, "_approve_locks", None)
    if locks is None:
        request.app.state._approve_locks = {}
        locks = request.app.state._approve_locks
    if request_id not in locks:
        locks[request_id] = asyncio.Lock()
    return locks[request_id]


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

    # Only known scopes may be requested — reject unknown ones up front so
    # the admin is never shown (and can never approve) a scope the system
    # does not actually enforce.
    unknown = sorted(set(body.requested_scopes) - VALID_SCOPES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown scopes: {unknown}; valid: {sorted(VALID_SCOPES)}",
        )

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

    # Surface the request as a non-blocking bell + toast notification. The
    # data payload carries everything the inline consent actions need to
    # approve/deny without re-fetching. Best effort: a notification failure
    # must not fail the created request (mirrors decisions.py).
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        try:
            scopes = record["requested_scopes"] or []
            await notifs.add(
                title="Access request",
                message=f"{record['identity_claim']} is requesting {', '.join(scopes)}",
                level="info",
                source="auth_requests",
                data={
                    "request_id": record["id"],
                    "identity_claim": record["identity_claim"],
                    "framework": record["framework"],
                    "requested_scopes": list(scopes),
                },
            )
        except Exception:
            pass

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

    # Serialise concurrent approvals for the same request to prevent orphaned
    # registry entries and duplicate grants from a TOCTOU race.
    lock = _get_approve_lock(request, request_id)
    async with lock:
        try:
            return await _do_approve(request, request_id, body, user)
        finally:
            # The request is now terminally decided (or errored); drop its lock so
            # request.app.state._approve_locks does not grow unbounded over the
            # process lifetime. A concurrent waiter already holds a reference to the
            # lock object, so popping the dict entry is safe.
            locks = getattr(request.app.state, "_approve_locks", None)
            if locks is not None:
                locks.pop(request_id, None)


async def approve_request_record(
    request: Request,
    *,
    record: dict,
    granted_scopes: list[str],
    effective_project: str | None,
    decided_by: str,
    project_id: str | None = None,
) -> dict:
    """Register an agent, mint its token, write grants + relationships +
    membership + a2a sync, and record the decision.

    This is the single mint machinery shared by the consent approve route
    (``_do_approve``, which resolves ``granted_scopes``/``effective_project``
    from the admin's ``ApproveBody``) and the project-invite redeem path
    (``project_invites.redeem``), which already has the scopes + bound project
    from the invite. ``decided_by`` is the actor recorded on the decision; for
    the consent route it is the approving admin's user_id, for invite auto-mode
    it is the invite's ``created_by``.

    Returns ``{"status": "accepted", "canonical_id": ...}``.

    Raises ``HTTPException`` for the same guard failures as the consent route:
    a project-scoped grant without a project_id (400), or an active-handle
    collision (409).
    """
    auth_store = _get_auth_requests_store(request)

    # The admin can narrow the requested scopes but never widen them, and
    # every granted scope must be in the closed vocabulary (defence in depth
    # for pending records created before scope validation existed).
    requested = set(record["requested_scopes"] or [])
    granted = set(granted_scopes)
    invalid = sorted((granted - requested) | (granted - VALID_SCOPES))
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=(
                f"granted scopes must be a subset of the requested scopes; "
                f"not grantable: {invalid}"
            ),
        )

    _CANVAS_SCOPES = {"canvas_read", "canvas_write"}
    _PROJECT_SCOPES = {"project_tasks"} | _CANVAS_SCOPES

    # project_tasks and the canvas scopes bind the token to a specific project
    # and add a membership row, so require a real project_id for these grants.
    # The check uses ``project_id`` — the EXPLICIT picker value the human (or
    # the invite) supplied — NOT the agent-supplied fallback that produced
    # ``effective_project``. Never fall back to the agent-supplied project_id for
    # these grants: POST /api/agents/auth-requests is unauthenticated, so the
    # request could name any existing project the operator never validated. A
    # blank/None project_id is not a real binding and must fail closed exactly
    # like a missing one; the redeem path passes the invite's project (always
    # non-empty for a project invite), so it passes this guard.
    needs_project = bool(set(granted_scopes) & _PROJECT_SCOPES)
    if needs_project and not (project_id and project_id.strip()):
        missing = sorted(set(granted_scopes) & _PROJECT_SCOPES)
        raise HTTPException(
            status_code=400,
            detail=f"project_id is required when granting {missing}",
        )

    registry = _get_registry_store(request)
    private_pem, _public_pem = _get_keypair(request)
    grants_store = _get_grants_store(request)
    rel_mgr = _get_relationships(request)

    # Mint canonical identity in the registry.
    # Strip whitespace first, then remove the leading "@" sigil (bus-addressing
    # syntax only), then strip again.  If that leaves nothing (claim was "@" or
    # whitespace-only) fall back to the framework name -- never the raw claim,
    # which could still carry the "@", so display_name is always a clean,
    # non-empty value.
    _claim = record["identity_claim"].strip().removeprefix("@").strip()
    display_name = _claim or record["framework"]

    handle = _slugify(_claim)
    existing_active = await registry.get_by_handle(handle, status="active")
    if existing_active is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"handle '{handle}' is already in use by active agent "
                f"{existing_active['canonical_id']}; pick a different identity_claim"
            ),
        )

    # Register with the handle SET at birth. external-selfjoin agents are born
    # 'pending' (governance lifecycle), so the partial unique index
    # ux_agent_active_handle (active + non-empty handle) does not fire at INSERT
    # time; it fires only when we flip the row to 'active' below.  That removes
    # the old window (register handle='' -> set active -> update handle) in which
    # an agent could sit ACTIVE with an empty handle, and lets SQLite reject a
    # duplicate active handle the instant a concurrent approve tries to take it.
    canonical_id = None
    try:
        reg_record = await registry.register(
            framework=record["framework"],
            display_name=display_name,
            user_id=decided_by,
            # external-selfjoin is the only origin that births the agent
            # 'pending' (governance lifecycle) so the pending -> active
            # transition below is the activation. The provenance of WHO
            # approved (admin consent vs project invite) lives on the
            # auth-request record / invite, not the registry origin column.
            origin="external-selfjoin",
            handle=handle,
        )
        canonical_id = reg_record["canonical_id"]

        # Consent approval IS the activation. external-selfjoin agents are born
        # 'pending' (governance lifecycle); approving the auth-request transitions
        # them to 'active' so they are NOT in the bus inactive/revocation feed and
        # @taOSmd's identity-AND-grant gate accepts them.
        await registry.set_status(canonical_id, "active", actor=decided_by)
    except IntegrityError:
        # A concurrent approve already took this active handle (the partial
        # unique index fired). Roll back the failed write and remove the
        # half-registered pending row so we never leave an active-without-handle
        # agent or a stale pending row. Return the same friendly 409.
        try:
            await registry.rollback()
        except Exception:  # noqa: BLE001 - never mask the 409 below
            pass
        if canonical_id is not None:
            try:
                await registry.delete(canonical_id)
            except Exception:  # noqa: BLE001 - never mask the 409 below
                pass
        raise HTTPException(
            status_code=409,
            detail=(
                f"handle '{handle}' is already in use by active agent; "
                f"pick a different identity_claim"
            ),
        )

    # Issue the identity token.
    token = mint_registry_token(
        canonical_id,
        private_pem,
        user_id=decided_by,
        framework=record["framework"],
        project_id=effective_project,
    )

    # Record grants for each approved scope.
    for scope in granted_scopes:
        await grants_store.add_grant(canonical_id, scope, tier="once", project_id=effective_project)
        # Also write a RelationshipManager permission edge so the existing
        # permission-check path (can_communicate etc.) is aware of the agent.
        await rel_mgr.set_permission(canonical_id, "taos-instance", scope)

    # If the agent was granted project_tasks and bound to a project, add it as a
    # member of that project so it shows up in the project's Members and joins the
    # project a2a channel (membership is synced into the channel). Best-effort: a
    # membership failure never blocks the approval, which the token + grant already
    # authorize.
    if effective_project and set(granted_scopes) & _PROJECT_SCOPES:
        try:
            pstore = getattr(request.app.state, "project_store", None)
            if pstore is not None:
                granted_canvas = set(granted_scopes) & _CANVAS_SCOPES
                # project_tasks and canvas scopes both bind the token to a
                # project and require a membership row. The project_tasks path
                # adds plain membership; canvas scopes additionally flip the
                # per-member can_read_canvas / can_edit_canvas flags so the
                # approved token is actually authorized for canvas calls
                # (an inert member row with all canvas flags 0 would 403).
                # Only flags that were explicitly granted are set, mirroring
                # how the consent card scopes are narrowed.
                if "project_tasks" in granted_scopes or granted_canvas:
                    await pstore.add_member(
                        project_id=effective_project,
                        member_id=canonical_id,
                        member_kind="native",
                        role="member",
                    )
                    if granted_canvas:
                        await pstore.set_member_canvas(
                            project_id=effective_project,
                            member_id=canonical_id,
                            can_read=("canvas_read" in granted_canvas),
                            can_write=("canvas_write" in granted_canvas),
                        )
                    if "project_tasks" in granted_scopes:
                        from tinyagentos.projects.a2a import ensure_a2a_channel

                        await ensure_a2a_channel(
                            request.app.state.chat_channels,
                            pstore,
                            effective_project,
                            config=getattr(request.app.state, "config", None),
                        )
        except Exception:  # noqa: BLE001 - membership is best-effort, never blocks approval
            logger.warning(
                "auth-approve: could not sync %s membership/a2a channel for project %s",
                canonical_id,
                effective_project,
                exc_info=True,
            )

    # Atomically commit the decision.
    result = await auth_store.set_decision(
        record["id"],
        "accepted",
        canonical_id=canonical_id,
        token=token,
        granted_scopes=granted_scopes,
        decided_by=decided_by,
    )
    if result is None:
        # Another concurrent approve beat us — 409.
        raise HTTPException(
            status_code=409,
            detail="request was decided concurrently; check current status",
        )

    await _retire_request_notification(request, record["id"])
    return {"status": "accepted", "canonical_id": canonical_id}


async def _do_approve(request: Request, request_id: str, body: ApproveBody, user):
    """Inner approve logic — called under a per-request lock.

    Resolves the admin's approve decision into the shared
    ``approve_request_record`` mint machinery (used by both this route and the
    project-invite redeem path). No behaviour change versus the pre-extraction
    route; it only forwards the resolved ``granted_scopes`` / ``effective_project``.
    """
    auth_store = _get_auth_requests_store(request)
    record = await auth_store.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="request not found")
    if record["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"request is already {record['status']!r}; cannot approve",
        )

    # Resolve effective project binding: admin override wins; fall back to the
    # project_id the agent requested (may be None for a global token).
    effective_project = (
        body.project_id if body.project_id is not None else record.get("project_id")
    )

    return await approve_request_record(
        request,
        record=record,
        granted_scopes=body.granted_scopes,
        effective_project=effective_project,
        decided_by=user.user_id,
        project_id=body.project_id,
    )


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

    await _retire_request_notification(request, request_id)
    return {"status": "refused"}


@router.get("/api/agents/auth-requests")
async def list_auth_requests(
    request: Request,
    status: Optional[str] = "pending",
    user: CurrentUser = Depends(current_user),
):
    """List pending auth requests (admin only).

    This is the feed the desktop notification / Permissions app reads.
    Phase 1 only supports status=pending (the default).
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    if status is not None and status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"status={status!r} is not supported in Phase 1; omit or pass status=pending",
        )

    store = _get_auth_requests_store(request)
    pending = await store.list_pending()
    return {"requests": pending}
