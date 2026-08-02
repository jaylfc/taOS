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
from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin

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
    # Authoring cards, deliberately SEPARATE from project_tasks. project_tasks
    # is documented and tested as read + lifecycle + comments only ("Invariant
    # 2 + 5"), so widening it would retroactively grant authoring to every agent
    # already approved for it, invisibly to whoever approved them. A distinct
    # scope keeps an existing approval meaning exactly what it meant when it was
    # given, and makes authoring an explicit per-agent opt-in.
    "project_tasks_create",
    # Editing a card's own fields (title, body, labels, status, priority).
    # Distinct from project_tasks (read + lifecycle + comments) and
    # project_tasks_create (authoring): an agent may PATCH only its OWN cards or
    # those on a project it leads, and only the whitelisted fields.
    "project_tasks_update",
    # Canvas access: read and write on a specific project's canvas. Like
    # project_tasks, a project_id is required so the token is bound to the
    # operator-validated project rather than whatever the unauthenticated agent
    # named in the request.
    "canvas_read",
    "canvas_write",
    # Raise decisions in the Decisions app: decisions_write (post), decisions_read
    # (list own). A global (null-project) grant authorizes OS-level decisions; a
    # per-project grant authorizes that project only. The route verifies the grant.
    "decisions_read",
    "decisions_write",
    # Doc-review stamps: read/set review state on a project's docs
    # (project-bound like project_tasks). Reconciled from master at beta.45 -
    # the routes shipped on every install while the scope was missing here.
    "project_doc_review",
    "observatory_control",
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


class AssignAgentBody(BaseModel):
    canonical_id: str
    scopes: list[str]
    is_lead: bool = False


class CreateScopeRequest(BaseModel):
    """Body for POST /api/agents/registry/{canonical_id}/scope-requests.

    An ALREADY-REGISTERED agent (or its owner/admin) asks for additional scope
    grants on that same identity. No new identity is minted on approval.
    """
    requested_scopes: list[str]
    project_id: Optional[str] = None
    reason: str = ""


class ApproveScopeBody(BaseModel):
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


def _get_scope_requests_store(request: Request):
    store = getattr(request.app.state, "agent_scope_requests", None)
    if store is None:
        raise RuntimeError("agent_scope_requests store not on app.state")
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


async def _retire_scope_request_notification(request: Request, request_id: str) -> None:
    """Archive the bell notification for a now-decided scope request so it leaves
    the active list. Best effort: never fails the decision."""
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is None:
        return
    try:
        await notifs.archive_by_source_ref("agent_scope_requests", request_id)
    except Exception:
        pass


# Single source of truth for which scopes are meaningless without a project
# binding. This lived in three places (two function-local copies plus a module
# one), and fixing only one of them left the auth-request approval path -- the
# path an invite actually takes -- still granting files_* and
# project_tasks_create globally. One definition, referenced everywhere.
_CANVAS_SCOPES = {"canvas_read", "canvas_write"}
_FILES_SCOPES = {"files_read", "files_write"}
_PROJECT_SCOPES = {"project_tasks", "project_tasks_create", "project_tasks_update"} | _CANVAS_SCOPES | _FILES_SCOPES


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
    display_name: str | None = None,
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
    # An explicit display_name (e.g. the OS-level invite alias) wins so the human
    # label is preserved even when the handle is slugified/deduped; otherwise fall
    # back to the cleaned claim, then the framework name.
    display_name = (display_name or "").strip() or _claim or record["framework"]

    handle = _slugify(_claim)
    existing_active = await registry.get_by_handle(handle, status="active")
    if existing_active is not None:
        # The handle already maps to an ACTIVE identity. In the multi-project
        # model (taOS #1862) this is NOT a hard collision when the approval is
        # for a project-scoped grant: instead of 409ing, ADD the existing agent
        # to the new project (reuse its identity + token, do not re-register).
        # A non-project approval colliding on a handle is still a genuine
        # identity collision and stays a 409.
        # Bind the multi-project ADD strictly to the EXPLICIT validated
        # project_id, never to effective_project. The guard above already
        # rejected a project-scoped grant with an absent project_id, so for
        # project scopes project_id is the operator/invite-validated value;
        # binding to effective_project (which can carry the agent-supplied
        # fallback for global scopes) would be safe only by invariant. Gate and
        # bind on project_id so cross-project escalation is impossible by
        # construction (kilo review, taOS #1862).
        if project_id and set(granted_scopes) & _PROJECT_SCOPES:
            existing_cid = existing_active["canonical_id"]
            token = mint_registry_token(
                existing_cid,
                private_pem,
                user_id=decided_by,
                framework=record["framework"],
                project_id=project_id,
            )
            await add_agent_to_project(
                request,
                canonical_id=existing_cid,
                project_id=project_id,
                granted_scopes=granted_scopes,
                decided_by=decided_by,
            )
            result = await auth_store.set_decision(
                record["id"],
                "accepted",
                canonical_id=existing_cid,
                token=token,
                granted_scopes=granted_scopes,
                decided_by=decided_by,
            )
            if result is None:
                raise HTTPException(
                    status_code=409,
                    detail="request was decided concurrently; check current status",
                )
            await _retire_request_notification(request, record["id"])
            return {"status": "accepted", "canonical_id": existing_cid}
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


async def add_agent_to_project(
    request: Request,
    *,
    canonical_id: str,
    project_id: str,
    granted_scopes: list[str],
    decided_by: str,
    is_lead: bool = False,
) -> dict:
    """Add an ALREADY-REGISTERED agent to ANOTHER project (taOS #1862).

    This is the shared primitive for the multi-project identity model: it
    writes the per-scope grants bound to *project_id*, adds the idempotent
    project_members row, ensures the a2a channel membership, and records the
    relationship permission edge. It does NOT mint a new token or re-register
    the agent: the identity is reused so one registry JWT spans every project
    the agent holds a grant for.

    Returns ``{"canonical_id": ..., "project_id": ..., "granted_scopes": ...}``.
    """
    grants_store = _get_grants_store(request)
    rel_mgr = _get_relationships(request)


    # Write the grants bound to this project and the relationship edge.
    for scope in granted_scopes:
        await grants_store.add_grant(
            canonical_id, scope, tier="once", project_id=project_id
        )
        await rel_mgr.set_permission(canonical_id, "taos-instance", scope)

    # If a project-scoped scope was granted, add the agent as a project member
    # (PK (project_id, member_id) makes this naturally idempotent) and sync the
    # a2a channel. Best-effort: a membership failure never blocks the grant,
    # which already authorizes the agent for the project.
    result: dict = {
        "canonical_id": canonical_id,
        "project_id": project_id,
        "granted_scopes": list(granted_scopes),
        "is_lead": bool(is_lead),
    }
    if set(granted_scopes) & _PROJECT_SCOPES:
        try:
            pstore = getattr(request.app.state, "project_store", None)
            if pstore is not None:
                granted_canvas = set(granted_scopes) & _CANVAS_SCOPES
                role = "lead" if is_lead else "member"
                if "project_tasks" in granted_scopes or granted_canvas:
                    await pstore.add_member(
                        project_id=project_id,
                        member_id=canonical_id,
                        member_kind="native",
                        role=role,
                    )
                    if granted_canvas:
                        await pstore.set_member_canvas(
                            project_id=project_id,
                            member_id=canonical_id,
                            can_read=("canvas_read" in granted_canvas),
                            can_write=("canvas_write" in granted_canvas),
                        )
                    # role="lead" above is only a LABEL on the member row. The
                    # project's actual lead is the projects.lead_member_id
                    # pointer, and set_lead is its only writer, so without this
                    # call the member reads as lead in the UI while every
                    # lead-gated check refuses them (taOS #2113: exactly what
                    # happened to Hermes on taOSrabbit). Best-effort like the
                    # rest of this block: the membership + grant already stand.
                    if is_lead:
                        try:
                            await pstore.set_lead(project_id, canonical_id)
                        except KeyError:
                            logger.warning(
                                "add_agent_to_project: could not set %s as lead of %s",
                                canonical_id, project_id,
                            )

                    if "project_tasks" in granted_scopes:
                        from tinyagentos.projects.a2a import ensure_a2a_channel

                        await ensure_a2a_channel(
                            request.app.state.chat_channels,
                            pstore,
                            project_id,
                            config=getattr(request.app.state, "config", None),
                        )
        except Exception:  # noqa: BLE001 - membership is best-effort
            logger.warning(
                "add_agent_to_project: could not sync %s membership/a2a for project %s",
                canonical_id,
                project_id,
                exc_info=True,
            )
    return result


@router.post("/api/projects/{project_id}/members/assign-agent")
async def assign_agent_to_project(
    request: Request,
    project_id: str,
    body: AssignAgentBody,
    user: CurrentUser = Depends(current_user),
):
    """Admin route: assign an EXISTING agent (by canonical_id) to a project.

    The Agents-app UI (a later slice) uses this to add an already-registered
    agent to another project without re-registering it or minting a new token.
    Writes the per-scope grants bound to the project, the idempotent
    project_members row, and ensures a2a channel membership.

    Owner-or-admin gated on the project like the invite mint route.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    pstore = getattr(request.app.state, "project_store", None)
    if pstore is None:
        raise HTTPException(status_code=500, detail="project store unavailable")
    project = await pstore.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    require_owner_or_admin(user, project["user_id"])

    # Every granted scope must be in the closed vocabulary.
    unknown = sorted(set(body.scopes) - VALID_SCOPES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown scopes: {unknown}; valid: {sorted(VALID_SCOPES)}",
        )

    registry = _get_registry_store(request)
    existing = await registry.get(body.canonical_id)
    if existing is None:
        raise HTTPException(
            status_code=404, detail="agent canonical_id not found in the registry"
        )

    result = await add_agent_to_project(
        request,
        canonical_id=body.canonical_id,
        project_id=project_id,
        granted_scopes=body.scopes,
        decided_by=user.user_id,
        is_lead=body.is_lead,
    )
    return result


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


# ---------------------------------------------------------------------------
# Scope requests — an EXISTING agent asks for MORE scopes on its own identity.
#
# Unlike the auth-request flow above, approval here does NOT mint a new
# identity: it adds grants to the existing canonical_id. Creation is gated to
# the agent's OWN registry token (self-request) OR the owner/admin, because the
# agent already has credentials — an anonymous caller must never be able to
# escalate an existing identity. Approve/deny are owner/admin only.
# ---------------------------------------------------------------------------

# Maximum unresolved scope requests per canonical_id before new ones are 429'd.
_SCOPE_REQUEST_PENDING_CAP = 10

# Scopes that bind a grant to a specific project (and add a membership row), so
# a real project_id is required for them. decisions_read/decisions_write are
# NOT here: they may be granted globally (project_id=None) or per-project.
#
# These are aliases, not copies. Missing a scope here is not harmless: the
# approval would succeed with no project_id, the grant would be written global
# (project_id=None), and check_agent_scope_for_project would then never match it
# -- so the operator believes they granted access and the agent silently has
# none. Re-listing the members instead of aliasing is how the three earlier
# copies drifted apart, so keep these as plain aliases.
_SCOPE_CANVAS_SCOPES = _CANVAS_SCOPES
_SCOPE_FILES_SCOPES = _FILES_SCOPES
_SCOPE_PROJECT_SCOPES = _PROJECT_SCOPES


async def _authorize_scope_request_creation(
    request: Request, canonical_id: str, record: dict
) -> None:
    """Authorize the caller to create a scope request for *canonical_id*.

    Allowed: an owner/admin session (or admin local token), OR the agent's own
    registry bearer token whose ``sub`` matches *canonical_id*. Any other caller
    (including a different agent's token, or no credentials) is a 403/401.
    """
    is_admin = bool(getattr(request.state, "is_admin", False))
    uid = getattr(request.state, "user_id", None)
    if is_admin or (uid and uid == record.get("user_id")):
        return

    # The agent's own registry token. check_agent_identity returns None when no
    # Authorization header is present (an unauthenticated caller never reaches
    # here anyway — the middleware 401s a credential-less non-exempt request) and
    # raises 401/403 for a malformed/inactive token.
    from tinyagentos.agent_token_auth import check_agent_identity

    agent_cid = await check_agent_identity(request)
    if agent_cid is not None and agent_cid == canonical_id:
        return

    raise HTTPException(status_code=403, detail="forbidden")


@router.post("/api/agents/registry/{canonical_id}/scope-requests")
async def create_scope_request(
    request: Request, canonical_id: str, body: CreateScopeRequest
):
    """Create a pending scope request for an EXISTING active agent.

    Auth: the agent's own registry bearer token (sub == canonical_id) OR the
    owning user / an admin. Returns {request_id, status: 'pending'}.
    """
    registry = _get_registry_store(request)
    record = await registry.get(canonical_id)
    if record is None or record.get("status") != "active":
        # Existence-hiding is unnecessary here (the caller must already be the
        # agent or its owner/admin) but an inactive/unknown id is simply a 404.
        raise HTTPException(
            status_code=404, detail="agent not found or not active"
        )

    # Authorize BEFORE any request-shape validation so an unauthorized caller
    # cannot probe scope validity (an authz failure must not leak whether a
    # scope name is valid). The record fetch above is the minimum needed to
    # authorize against the agent's owner.
    await _authorize_scope_request_creation(request, canonical_id, record)

    if not body.requested_scopes:
        raise HTTPException(status_code=400, detail="requested_scopes must not be empty")

    # Only known scopes may be requested — an unknown scope can never be enforced.
    unknown = sorted(set(body.requested_scopes) - VALID_SCOPES)
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"unknown scopes: {unknown}; valid: {sorted(VALID_SCOPES)}",
        )

    store = _get_scope_requests_store(request)
    pending_count = await store.count_pending_for(canonical_id)
    if pending_count >= _SCOPE_REQUEST_PENDING_CAP:
        raise HTTPException(
            status_code=429,
            detail=(
                f"too many pending scope requests for {canonical_id!r} "
                f"({pending_count} pending; resolve existing requests first)"
            ),
        )

    rec = await store.create(
        canonical_id=canonical_id,
        requested_scopes=body.requested_scopes,
        project_id=body.project_id,
        reason=body.reason,
    )

    # Surface the request as a bell + toast for the owner/admin. Best effort: a
    # notification failure must not fail the created request (mirrors the
    # auth-request create flow and decisions.py).
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        try:
            scopes = rec["requested_scopes"] or []
            handle = record.get("handle") or record.get("display_name") or canonical_id
            where = f"project {rec['project_id']}" if rec.get("project_id") else "global scope"
            await notifs.add(
                title="Scope request",
                message=f"{handle} is requesting {', '.join(scopes)} on {where}",
                level="info",
                source="agent_scope_requests",
                data={
                    "request_id": rec["id"],
                    "canonical_id": canonical_id,
                    "requested_scopes": list(scopes),
                    "project_id": rec.get("project_id"),
                },
            )
        except Exception:
            pass

    return {"request_id": rec["id"], "status": "pending"}


@router.post("/api/agents/registry/{canonical_id}/scope-requests/{req_id}/approve")
async def approve_scope_request(
    request: Request,
    canonical_id: str,
    req_id: str,
    body: ApproveScopeBody,
    user: CurrentUser = Depends(current_user),
):
    """Approve a scope request: add the granted scopes to the EXISTING agent.

    Owner/admin only. The admin may narrow but never widen the requested scopes.
    Grants are idempotent (UNIQUE(canonical_id, scope, project_id)), so
    re-approving an already-granted scope is a no-op, not an error. No new
    identity is minted.
    """
    registry = _get_registry_store(request)
    record = await registry.get(canonical_id)
    if record is None or record.get("status") != "active":
        raise HTTPException(status_code=404, detail="agent not found or not active")
    require_owner_or_admin(user, record["user_id"])

    store = _get_scope_requests_store(request)

    # Serialize concurrent approvals of the SAME request (mirror the consent
    # approve path). All the state-changing work runs inside the lock with a
    # pending re-check, so two racing approvals cannot both write grants and flip
    # the decision. Grants are written before the decision flip; that ordering is
    # safe only under this lock plus idempotent add_grant.
    lock = _get_approve_lock(request, req_id)
    async with lock:
        req = await store.get(req_id)
        if req is None or req.get("canonical_id") != canonical_id:
            raise HTTPException(status_code=404, detail="scope request not found")
        if req["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"request is already {req['status']!r}; cannot approve",
            )

        if not body.granted_scopes:
            raise HTTPException(
                status_code=400, detail="granted_scopes must not be empty"
            )

        # Narrow-not-widen: granted must be a subset of the requested scopes, and
        # every granted scope must be in the closed vocabulary.
        requested = set(req["requested_scopes"] or [])
        granted = set(body.granted_scopes)
        invalid = sorted((granted - requested) | (granted - VALID_SCOPES))
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"granted scopes must be a subset of the requested scopes; "
                    f"not grantable: {invalid}"
                ),
            )

        # Project binding: ONLY the operator's explicit picker value binds a grant
        # to a project. NEVER fall back to the agent-named req.project_id -- an
        # agent can self-request (see _authorize_scope_request_creation), so the
        # request could name any project the operator never validated, and a
        # global-capable scope (decisions_*) silently bound to it would be a
        # cross-project escalation. A global-capable scope with no operator
        # project is granted globally (project_id=None).
        validated_project = (
            body.project_id if (body.project_id and body.project_id.strip()) else None
        )

        # project_tasks and the canvas scopes bind the grant to a specific project
        # and add a membership row, so they require an explicit validated project.
        needs_project = bool(granted & _SCOPE_PROJECT_SCOPES)
        if needs_project and validated_project is None:
            missing = sorted(granted & _SCOPE_PROJECT_SCOPES)
            raise HTTPException(
                status_code=400,
                detail=f"project_id is required when granting {missing}",
            )

        grants_store = _get_grants_store(request)
        rel_mgr = _get_relationships(request)

        # Global (non-project) grants: write the grant + relationship edge bound
        # to the validated project (None = global). Project-scoped grants: route
        # through add_agent_to_project so the membership + a2a channel are synced,
        # exactly like the consent-approve path. add_grant is idempotent, so
        # re-approving an already-granted scope changes nothing.
        project_scopes = [
            s for s in body.granted_scopes if s in _SCOPE_PROJECT_SCOPES
        ]
        other_scopes = [
            s for s in body.granted_scopes if s not in _SCOPE_PROJECT_SCOPES
        ]
        for scope in other_scopes:
            await grants_store.add_grant(
                canonical_id, scope, tier="once", project_id=validated_project
            )
            await rel_mgr.set_permission(canonical_id, "taos-instance", scope)
        if project_scopes:
            await add_agent_to_project(
                request,
                canonical_id=canonical_id,
                project_id=validated_project,
                granted_scopes=project_scopes,
                decided_by=user.user_id,
            )

        result = await store.set_decision(
            req_id,
            "accepted",
            granted_scopes=body.granted_scopes,
            decided_by=user.user_id,
        )
        if result is None:
            raise HTTPException(
                status_code=409,
                detail="request was decided concurrently; check current status",
            )

    await _retire_scope_request_notification(request, req_id)
    return {
        "status": "accepted",
        "canonical_id": canonical_id,
        "granted_scopes": body.granted_scopes,
    }


@router.post("/api/agents/registry/{canonical_id}/scope-requests/{req_id}/deny")
async def deny_scope_request(
    request: Request,
    canonical_id: str,
    req_id: str,
    user: CurrentUser = Depends(current_user),
):
    """Deny a pending scope request (owner/admin only)."""
    registry = _get_registry_store(request)
    record = await registry.get(canonical_id)
    if record is None:
        raise HTTPException(status_code=404, detail="agent not found")
    require_owner_or_admin(user, record["user_id"])

    store = _get_scope_requests_store(request)

    # Serialize against a concurrent approve of the same request (same per-req
    # lock the approve path uses), with a pending re-check inside the lock.
    lock = _get_approve_lock(request, req_id)
    async with lock:
        req = await store.get(req_id)
        if req is None or req.get("canonical_id") != canonical_id:
            raise HTTPException(status_code=404, detail="scope request not found")
        if req["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"request is already {req['status']!r}; cannot deny",
            )

        result = await store.set_decision(req_id, "refused", decided_by=user.user_id)
        if result is None:
            raise HTTPException(
                status_code=409,
                detail="request was decided concurrently; check current status",
            )

    await _retire_scope_request_notification(request, req_id)
    return {"status": "refused"}
