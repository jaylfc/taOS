from __future__ import annotations

"""Routes for user-to-user resource sharing.

POST   /api/shares            — share a resource with another user by username
GET    /api/shares            — list shares (direction=out → owned, direction=in → received)
POST   /api/shares/{id}/accept — accept a pending share (target user only)
POST   /api/shares/{id}/deny   — deny a pending share (target user only)
DELETE /api/shares/{id}       — revoke a share (owner or admin)

The consent loop mirrors the external-agent consent pattern in
``agent_auth_requests.py``: on share-create a notification is raised to the
target user and a Decision record is created so the desktop consent actions
can approve / deny later.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class CreateShareRequest(BaseModel):
    resource_type: str
    resource_id: str
    to_username: str
    permission: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_shares_store(request: Request):
    store = getattr(request.app.state, "user_shares", None)
    if store is None:
        raise RuntimeError("user_shares store not on app.state")
    return store


async def user_can_access(
    request: Request, resource_type: str, resource_id: str, user_id: str
) -> bool:
    """Module-level helper so route consumers can check share access.

    Returns True if *user_id* has at least one active, non-expired share
    for (*resource_type*, *resource_id*).  Importable from
    ``tinyagentos.routes.user_shares``.
    """
    store = _get_user_shares_store(request)
    return await store.user_can_access(resource_type, resource_id, user_id)


async def _find_share_by_id(request: Request, share_id: int) -> dict | None:
    """Look up a share by id via a direct primary-key lookup."""
    store = _get_user_shares_store(request)
    return await store.get_share(share_id)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/api/shares")
async def create_share(
    request: Request,
    body: CreateShareRequest,
    user: CurrentUser = Depends(current_user),
):
    """Share a resource with another user by username.

    Resolves *to_username* via the AuthManager → 404 if not found.
    Duplicate share (same owner + resource + target + permission) is
    idempotent — the store replaces the existing row.

    On create, raises a notification and a Decision record to the target
    user so the desktop consent actions can approve / deny.
    """
    store = _get_user_shares_store(request)

    # Resolve target user by username.
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        raise HTTPException(status_code=500, detail="auth manager not available")

    target = auth.find_user(body.to_username)
    if target is None:
        raise HTTPException(status_code=404, detail=f"user '{body.to_username}' not found")

    target_user_id: str = target["id"]

    # Guard against self-share — creates a confusing UX and an unnecessary
    # Decision against yourself.
    if target_user_id == user.user_id:
        raise HTTPException(status_code=400, detail="cannot share with yourself")

    # Create (or replace) the share.  The store's write lock makes this
    # idempotent for concurrent same-key writes.
    record = await store.add_share(
        owner_user_id=user.user_id,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        shared_with_user_id=target_user_id,
        permission=body.permission,
    )

    # ------------------------------------------------------------------
    # Consent wiring — notification (same pattern as agent_auth_requests.py
    # lines 204-221).  Best effort: a notification failure must not fail the
    # created share.
    # ------------------------------------------------------------------
    notifs = getattr(request.app.state, "notifications", None)
    if notifs is not None:
        try:
            await notifs.add(
                title="Resource shared with you",
                message=(
                    f"{user.user_id} shared {body.resource_type}/{body.resource_id} "
                    f"with you (permission: {body.permission})"
                ),
                level="info",
                source="user_shares",
                user_id=target_user_id,
                data={
                    "share_id": record["id"],
                    "owner_user_id": user.user_id,
                    "resource_type": body.resource_type,
                    "resource_id": body.resource_id,
                    "permission": body.permission,
                },
            )
        except Exception:
            logger.warning(
                "Failed to create notification for share %s → user %s",
                record.get("id"), target_user_id, exc_info=True,
            )

    # ------------------------------------------------------------------
    # Consent wiring — Decision record for the target user's Decisions
    # inbox so desktop consent actions (approve/deny) can act on it.
    # ------------------------------------------------------------------
    decision_store = getattr(request.app.state, "decision_store", None)
    if decision_store is not None:
        try:
            await decision_store.create(
                from_agent=user.user_id,
                question=(
                    f"{user.user_id} shared {body.resource_type}/{body.resource_id} "
                    f"with you (permission: {body.permission})"
                ),
                type="approve_deny",
                user_id=target_user_id,
                context=f"Resource share from {user.user_id}",
                metadata={
                    "share_id": record["id"],
                    "owner_user_id": user.user_id,
                    "resource_type": body.resource_type,
                    "resource_id": body.resource_id,
                    "permission": body.permission,
                },
            )
        except Exception:
            logger.warning(
                "Failed to create Decision record for share %s → user %s",
                record.get("id"), target_user_id, exc_info=True,
            )

    return record


@router.get("/api/shares")
async def list_shares(
    request: Request,
    direction: str = Query("out", pattern="^(out|in)$"),
    user: CurrentUser = Depends(current_user),
):
    """List shares for the authenticated user.

    *direction=out* (default): shares the user owns (what you've shared).
    *direction=in*: shares where the user is the target (what's shared with you).
    """
    store = _get_user_shares_store(request)

    if direction == "in":
        return await store.list_shares_received(user.user_id)
    return await store.list_shares(user.user_id)


@router.delete("/api/shares/{share_id}")
async def revoke_share(
    request: Request,
    share_id: int,
    user: CurrentUser = Depends(current_user),
):
    """Revoke a share by id.  Owner or admin only.

    Loads the share first to obtain *owner_user_id*, then applies the
    ``require_owner_or_admin`` gate against it — the admin path covers
    removal of any share regardless of ownership.
    """
    store = _get_user_shares_store(request)

    target = await _find_share_by_id(request, share_id)
    if target is None:
        raise HTTPException(status_code=404, detail="share not found")

    # Owner or admin gate — checked against the share's owner_user_id, not
    # the caller's.  Admin covers removal of any share.
    require_owner_or_admin(user, target["owner_user_id"])

    await store.revoke_share(share_id)
    return {"status": "revoked", "share_id": share_id}


@router.post("/api/shares/{share_id}/accept")
async def accept_share(
    request: Request,
    share_id: int,
    user: CurrentUser = Depends(current_user),
):
    """Accept a pending share.  Target user only.

    The target user (shared_with_user_id) must accept before the share
    grants access.  Once accepted, ``user_can_access`` returns True.
    """
    store = _get_user_shares_store(request)

    target = await _find_share_by_id(request, share_id)
    if target is None:
        raise HTTPException(status_code=404, detail="share not found")

    if target["shared_with_user_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="only the target user may accept this share")

    if target.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"share is already {target.get('status', 'terminal')}")

    updated = await store.accept_share(share_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="share not found")

    return updated


@router.post("/api/shares/{share_id}/deny")
async def deny_share(
    request: Request,
    share_id: int,
    user: CurrentUser = Depends(current_user),
):
    """Deny a pending share.  Target user only.

    The target user (shared_with_user_id) can deny to reject the share.
    The share row is preserved with status='denied' for audit.
    """
    store = _get_user_shares_store(request)

    target = await _find_share_by_id(request, share_id)
    if target is None:
        raise HTTPException(status_code=404, detail="share not found")

    if target["shared_with_user_id"] != user.user_id:
        raise HTTPException(status_code=403, detail="only the target user may deny this share")

    if target.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"share is already {target.get('status', 'terminal')}")

    updated = await store.deny_share(share_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="share not found")

    return updated
