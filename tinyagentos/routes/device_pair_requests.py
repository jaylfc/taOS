from __future__ import annotations

"""Routes for the device pairing consent loop (taOS S4e).

POST /api/devices/pair-requests            -- unauthenticated; create a request + Decision
GET  /api/devices/pair-requests/{id}       -- unauthenticated; poll status / retrieve token

The opaque ``pair_request_id`` returned at creation acts as a capability for the
poll endpoint -- only the caller who received it can poll its status.  Approval is
surfaced through the existing Decisions system: creation raises a Decision whose
``metadata={kind: "device_pairing", pair_request_id}``; answering that Decision
with "approve" runs ``_apply_device_pairing_grant`` in routes/decisions.py, which
mints the device (bound to the ANSWERING user -- F1) and atomically transitions
THAT request (F2).  Denying the Decision transitions the same request to
"denied".  No separate approve/deny route exists.

Security notes (from the security review, bus 1490):
* verify_code is a human-comparison nonce only (F3): generated with ``secrets``,
  >= 6 digits, returned only on creation, never server-checked, never accepted
  as input.
* TOTAL pending cap mirrors the agent auth-request cap (F4); per-IP limiting
  alone is defeated by CGNAT / distributed floods.
* Minting reuses DeviceStore.register (taosdev_ token, per-user cap of 50,
  touch/last_seen, revoke, require_device -- all for free) with an
  ios/watchos/android platform whitelist (F5), guarded by a per-request approve
  lock to prevent a TOCTOU double-mint (F5).
* Expiry is enforced at APPROVE time, not merely hidden from the poll (F6).
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tinyagentos.device_pair_requests_store import (
    DevicePairRequestsStore,
    _PENDING_CAP,
    _live_status,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# F5: the store does not validate the platform, so the whitelist lives here.
_VALID_PLATFORMS = frozenset({"ios", "watchos", "android"})
_VERIFY_CODE_DIGITS = 6
_MAX_DISPLAY_NAME = 200


class CreatePairRequest(BaseModel):
    platform: str
    display_name: str = Field(default="", max_length=_MAX_DISPLAY_NAME)


def _get_pair_requests_store(request: Request) -> DevicePairRequestsStore:
    store = getattr(request.app.state, "device_pair_requests", None)
    if store is None:
        raise RuntimeError("device_pair_requests store not on app.state")
    return store


def _get_device_store(request: Request):
    store = getattr(request.app.state, "device_store", None)
    if store is None:
        raise RuntimeError("device_store not on app.state")
    return store


def _generate_verify_code() -> str:
    """A 6-digit human-comparison nonce (F3): never server-checked, never
    re-served except in the creation response."""
    # randbelow(900_000) + 100_000 yields 100000..999999 -- always 6 digits.
    return f"{secrets.randbelow(900_000) + 100_000:06d}"


def _requester_ip(request: Request) -> str:
    client = request.client
    if client is None:
        return ""
    return client.host or ""


def _admin_user_id(request: Request) -> str:
    """Resolve the instance user the pairing Decision is addressed to (the
    primary admin), mirroring the OS-level decider in routes/decisions.py."""
    users = request.app.state.auth.list_users()
    admins = [u for u in users if u.get("is_admin")]
    if not admins:
        return ""
    return admins[0].get("id") or ""


@router.post("/api/devices/pair-requests")
async def create_pair_request(request: Request, body: CreatePairRequest):
    """Submit a pairing request from an external device/app.

    No authentication required -- the device has no credentials yet.  Returns
    ``{pair_request_id, verify_code}`` and raises a blocking Decision to the
    instance admin surfacing the requester IP + platform.
    """
    # F5: platform whitelist (the store does not validate).
    if body.platform not in _VALID_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"platform must be one of {sorted(_VALID_PLATFORMS)}",
        )

    store = _get_pair_requests_store(request)

    if not _admin_user_id(request):
        raise HTTPException(
            status_code=409,
            detail="no admin exists to approve pairing requests",
        )

    verify_code = _generate_verify_code()
    requester_ip = _requester_ip(request)
    display = (body.display_name or "").strip() or body.platform

    # The lock is what makes the cap atomic; a store without it must fail
    # loudly rather than fall back to the racy count-then-create this fix
    # removed.
    async with store._create_lock:
        pending_count = await store.count_pending()
        if pending_count >= _PENDING_CAP:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"too many pending pair requests ({pending_count} pending; "
                    f"resolve existing requests first)"
                ),
            )
        record = await store.create(
            platform=body.platform,
            display_name=display,
            verify_code=verify_code,
            requester_ip=requester_ip,
        )
    pair_request_id = record["id"]

    # Raise a Decision to the instance admin.  The metadata binds the approval to
    # THIS pair_request (F2) so a racing attacker's request cannot ride the
    # victim's approval.  Approval is surfaced through Decisions exactly like the
    # agent consent loop -- no taOSgo-specific shortcut (F7).
    admin_id = _admin_user_id(request)
    decision_store = getattr(request.app.state, "decision_store", None)
    if decision_store is not None and admin_id:
        try:
            await decision_store.create(
                from_agent="@taOSc",
                question=(
                    f"taOSc on {display!r} wants to connect. Code {verify_code}. Allow?"
                ),
                type="approve_deny",
                priority="blocking",
                project_id=None,
                user_id=admin_id,
                metadata={
                    "kind": "device_pairing",
                    "pair_request_id": pair_request_id,
                },
                context=(
                    f"Pairing request from {body.platform} device "
                    f"at {requester_ip or 'unknown IP'}"
                ),
            )
        except Exception:
            logger.warning("device_pair_requests: could not raise Decision", exc_info=True)

        # Best-effort bell notification so the request leaves an auditable
        # trail in the inbox.  Must not fail the created request.
        notifs = getattr(request.app.state, "notifications", None)
        if notifs is not None:
            try:
                await notifs.add(
                    title="Pair request",
                    message=f"{display} wants to connect (code {verify_code})",
                    level="warning",
                    source="pair_requests",
                    user_id=admin_id,
                    data={"request_id": pair_request_id, "platform": body.platform},
                )
            except Exception:
                pass

    # F3 / criterion 5: verify_code is returned ONLY here -- never on the poll.
    return {"pair_request_id": pair_request_id, "verify_code": verify_code}


@router.get("/api/devices/pair-requests/{pair_request_id}")
async def get_pair_request(request: Request, pair_request_id: str):
    """Poll a pairing request's status.

    No authentication required -- the opaque pair_request_id is the capability.
    Returns ``{status}`` for pending/denied/expired, and additionally the device
    row + ``scoped_token`` on approval (the token is handed out once).
    """
    store = _get_pair_requests_store(request)
    record = await store.get(pair_request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="pair request not found")

    status = _live_status(record)
    result: dict = {"pair_request_id": pair_request_id, "status": status}

    if status == "accepted":
        device_id = record.get("device_id")
        if device_id:
            device_store = _get_device_store(request)
            device = await device_store.get(device_id)
            if device is not None:
                # The device row excludes the scoped_token; the token is the
                # one-time credential handed to the polling caller.
                device_safe = {k: v for k, v in device.items() if k != "scoped_token"}
                result["device"] = device_safe
                # scoped_token is released exactly once (F3 / design "ONCE").
                if not record.get("token_claimed"):
                    if await store.claim_scoped_token(pair_request_id):
                        result["scoped_token"] = device["scoped_token"]

    return result
