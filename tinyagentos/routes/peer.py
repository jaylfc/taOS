"""Peer channel routes — instance-to-instance API for contacts.

CSRF-exempt (bearer-only auth, no cookies). Peer tokens grant ONLY this
route family.

Endpoints
---------
POST /api/peer/inbox   — receive a signed envelope from a contact
POST /api/peer/chat    — receive a chat message envelope
POST /api/peer/ack     — acknowledge delivery of an envelope
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from tinyagentos.peer import (
    resolve_local_identity_id,
    verify_envelope,
    verify_envelope_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/peer", tags=["peer"])
# Auth is enforced centrally via the router-level ``_peer_auth_dep`` dependency
# (Kilo #2).  Route handlers read the authenticated ``contact_id`` from
# ``request.state.peer_contact_id`` — no per-route call to ``_authenticate_peer``
# is needed.  Adding a route here automatically gets bearer-auth.

# Rate limits per spec section 8: 60 req/min/contact, 32KB envelope cap.
_RATE_WINDOW_SECS = 60.0
_RATE_MAX_PER_WINDOW = 60
_MAX_ENVELOPE_BYTES = 32 * 1024  # 32 KB

# In-memory per-contact rate limiter: contact_id -> (window_start, count).
# Per-process only — under multi-worker deployments each worker maintains its
# own counter, so the effective aggregate limit is 60×N/min across N workers.
# FIXME(post-MVP): back with a shared store (Redis or sqlite contacts.db) for
# accurate cross-worker limits.  The current eviction (``_RATE_HITS_MAX_SIZE``)
# keeps memory bounded but does not coordinate across processes.
_rate_hits: dict[str, tuple[float, int]] = {}
# Max entries before stale-window cleanup triggers.  Keeps the dict bounded
# for long-running servers that see many distinct contacts over time.
_RATE_HITS_MAX_SIZE = 2000


def _rate_limit_ok(contact_id: str) -> bool:
    """Fixed-window per-contact rate limiter.

    Returns False when the contact has exceeded 60 requests in the current
    60-second window.  Evicts entries with expired windows when the dict
    grows past ``_RATE_HITS_MAX_SIZE`` to prevent unbounded memory growth.
    """
    now = time.time()
    window_start, count = _rate_hits.get(contact_id, (now, 0))
    if now - window_start >= _RATE_WINDOW_SECS:
        window_start, count = now, 0

    # Opportunistic eviction: when the dict exceeds the max size, sweep out
    # every entry whose window has expired.  This is O(n) but only runs
    # occasionally when the dict is full.
    if len(_rate_hits) >= _RATE_HITS_MAX_SIZE:
        expired = [
            cid for cid, (ws, _) in _rate_hits.items()
            if now - ws >= _RATE_WINDOW_SECS
        ]
        for cid in expired:
            del _rate_hits[cid]
        # LRU fallback (Kilo #5): when all windows are still active
        # (>2000 concurrent contacts each within 60s), evict the oldest
        # entry so the dict does not grow unbounded under sustained load.
        if len(_rate_hits) >= _RATE_HITS_MAX_SIZE:
            oldest = min(_rate_hits.keys(), key=lambda cid: _rate_hits[cid][0])
            del _rate_hits[oldest]

    count += 1
    _rate_hits[contact_id] = (window_start, count)
    return count <= _RATE_MAX_PER_WINDOW


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PeerEnvelope(BaseModel):
    """A signed envelope received from a remote contact."""

    envelope: dict = Field(..., description="The signed envelope dict")


class PeerAck(BaseModel):
    """Delivery acknowledgement."""

    envelope_id: str = Field(..., description="The nonce of the acknowledged envelope")
    contact_id: str = Field(..., description="The acknowledging contact")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def _authenticate_peer(request: Request) -> str:
    """Verify the bearer peer token and return the contact_id.

    Raises 401 if the token is missing, invalid, or the contact is not active.

    The peer token is stored hashed in peer_links.inbound_token_hash.  We
    look up the contact via an indexed hash lookup on the inbound_token_hash
    column (JOIN with contacts), so lookup is O(1) regardless of contact count.
    """
    store = request.app.state.contacts_store
    if store is None:
        raise HTTPException(status_code=503, detail="peer channel not available")

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer token required")

    presented = auth[7:].strip()
    if not presented:
        raise HTTPException(status_code=401, detail="empty bearer token")

    # Find the contact whose inbound token hash matches.
    contact = await store.find_contact_by_inbound_token(presented)
    if contact is None:
        raise HTTPException(status_code=401, detail="invalid peer token")

    contact_id = contact["contact_id"]
    return contact_id


async def _peer_auth_dep(request: Request) -> str:
    """Router-level dependency: authenticate EVERY /api/peer route.

    Kilo #2 — applying this as a router dependency ensures a future route
    added under /api/peer that forgets ``_authenticate_peer`` cannot become
    an unauthenticated surface.  The contact_id is stored on request.state
    so route handlers can read it directly.
    """
    contact_id = await _authenticate_peer(request)
    request.state.peer_contact_id = contact_id
    return contact_id


# Apply the dependency to the router now that _peer_auth_dep is defined.
router.dependencies.append(Depends(_peer_auth_dep))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/inbox")
async def peer_inbox(body: PeerEnvelope, request: Request):
    """Receive a signed envelope from a remote contact.

    The envelope body is verified against the sender's pinned Ed25519 pubkey
    (from the contacts store).  Rate-limited per contact.
    """
    contact_id = request.state.peer_contact_id
    store = request.app.state.contacts_store

    # Rate limit
    if not _rate_limit_ok(contact_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded (60/min/contact)")

    # Size limit — reject on Content-Length before we re-serialise.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            cl_int = int(cl)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if cl_int > _MAX_ENVELOPE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"request too large: {cl_int} bytes > {_MAX_ENVELOPE_BYTES}",
            )
    raw = json.dumps(body.envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > _MAX_ENVELOPE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"envelope too large: {len(raw)} bytes > {_MAX_ENVELOPE_BYTES}",
        )

    envelope = body.envelope

    # Verify sender binding: envelope["from"] must match the authenticated contact.
    from_field = envelope.get("from", "")
    if from_field != contact_id:
        raise HTTPException(
            status_code=403,
            detail=f"from field {from_field!r} does not match authenticated contact {contact_id!r}",
        )

    # Verify recipient: envelope["to"] must match the local hub identity.
    local_id = await asyncio.to_thread(resolve_local_identity_id, request.app.state.data_dir)
    if local_id is None:
        raise HTTPException(status_code=503, detail="hub identity not configured")
    to_field = envelope.get("to", "")
    if to_field != local_id:
        raise HTTPException(
            status_code=403,
            detail=f"envelope addressed to {to_field!r}, not {local_id!r}",
        )

    # Verify structure + freshness
    ok, err = verify_envelope(envelope)
    if not ok:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {err}")

    # Verify signature against pinned pubkey
    contact_rec = await store.get_contact(contact_id)
    if contact_rec is None:
        raise HTTPException(status_code=404, detail="contact not found")

    sender_pubkey = contact_rec.get("ed25519_pub", "")
    if not verify_envelope_signature(envelope, sender_pubkey):
        raise HTTPException(status_code=403, detail="invalid signature")

    # Nonce replay protection — record only AFTER all verification passes so
    # a rejected envelope (bad sig, bad recipient) does not burn its nonce.
    nonce = envelope.get("nonce", "")
    if not nonce:
        raise HTTPException(status_code=400, detail="missing nonce")
    kind = envelope.get("kind", "")
    if not await store.record_nonce(nonce, contact_id, kind):
        raise HTTPException(status_code=409, detail="nonce replay detected")

    # Mark peer as seen
    await store.mark_peer_seen(contact_id)

    # Dispatch the envelope by kind.
    kind = envelope.get("kind", "unknown")
    body_data = envelope.get("body", {})

    if kind == "collab_invite":
        return await _handle_collab_invite(request, contact_id, envelope, body_data)

    if kind in ("collab_invite_accept", "collab_invite_decline"):
        return await _handle_collab_response(request, contact_id, envelope, body_data, kind)

    # Log unrecognised kinds for debugging; they are accepted but not dispatched.
    logger.info(
        "peer_inbox: contact=%s kind=%s nonce=%s (unrecognised kind — accepted, no dispatch)",
        contact_id, kind, envelope.get("nonce", "?"),
    )

    return {"status": "received", "kind": kind, "nonce": envelope.get("nonce")}


@router.post("/chat")
async def peer_chat(body: PeerEnvelope, request: Request):
    """Receive a chat message envelope from a contact.

    Identical auth/envelope verification as /inbox, with a separate rate-limit
    path for chat-specific limits (600 msgs/day/contact per spec, but enforced
    per-minute for simplicity in v1).
    """
    contact_id = request.state.peer_contact_id
    store = request.app.state.contacts_store

    if not _rate_limit_ok(contact_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    # Size limit — reject on Content-Length before we re-serialise.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            cl_int = int(cl)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if cl_int > _MAX_ENVELOPE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"request too large: {cl_int} bytes > {_MAX_ENVELOPE_BYTES}",
            )
    raw = json.dumps(body.envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(raw) > _MAX_ENVELOPE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"envelope too large: {len(raw)} bytes > {_MAX_ENVELOPE_BYTES}",
        )

    envelope = body.envelope
    if envelope.get("kind") != "chat":
        raise HTTPException(status_code=400, detail="kind must be 'chat'")

    # Verify sender binding: envelope["from"] must match the authenticated contact.
    from_field = envelope.get("from", "")
    if from_field != contact_id:
        raise HTTPException(
            status_code=403,
            detail=f"from field {from_field!r} does not match authenticated contact {contact_id!r}",
        )

    # Verify recipient: envelope["to"] must match the local hub identity.
    local_id = await asyncio.to_thread(resolve_local_identity_id, request.app.state.data_dir)
    if local_id is None:
        raise HTTPException(status_code=503, detail="hub identity not configured")
    to_field = envelope.get("to", "")
    if to_field != local_id:
        raise HTTPException(
            status_code=403,
            detail=f"envelope addressed to {to_field!r}, not {local_id!r}",
        )

    ok, err = verify_envelope(envelope, expected_kind="chat")
    if not ok:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {err}")

    contact_rec = await store.get_contact(contact_id)
    if contact_rec is None:
        raise HTTPException(status_code=404, detail="contact not found")

    if not verify_envelope_signature(envelope, contact_rec.get("ed25519_pub", "")):
        raise HTTPException(status_code=403, detail="invalid signature")

    # Nonce replay protection — record only AFTER all verification passes.
    nonce = envelope.get("nonce", "")
    if not nonce:
        raise HTTPException(status_code=400, detail="missing nonce")
    kind = envelope.get("kind", "")
    if not await store.record_nonce(nonce, contact_id, kind):
        raise HTTPException(status_code=409, detail="nonce replay detected")

    await store.mark_peer_seen(contact_id)

    logger.info(
        "peer_chat: contact=%s nonce=%s",
        contact_id, envelope.get("nonce", "?"),
    )

    return {"status": "received", "nonce": envelope.get("nonce")}


@router.post("/ack")
async def peer_ack(body: PeerAck, request: Request):
    """Acknowledge delivery of an envelope (double-tick).

    The remote instance calls this after successfully processing an envelope
    so the sender can mark it as delivered.
    """
    contact_id = request.state.peer_contact_id
    store = request.app.state.contacts_store

    # The ack must come from the contact named in the body.
    if body.contact_id != contact_id:
        raise HTTPException(
            status_code=400,
            detail=f"contact_id {body.contact_id!r} does not match authenticated contact {contact_id!r}",
        )

    if not _rate_limit_ok(contact_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    # Nonce replay protection — record the ack's envelope_id as a nonce.
    # Without this a valid contact can replay POST /ack indefinitely.  A
    # replayed ack returns 409 Conflict (same contract as /inbox and /chat).
    if not await store.record_nonce(body.envelope_id, contact_id, "ack"):
        raise HTTPException(status_code=409, detail="ack replay detected")

    await store.mark_peer_seen(contact_id)

    logger.info(
        "peer_ack: contact=%s envelope_nonce=%s",
        contact_id, body.envelope_id,
    )

    return {"status": "acked", "envelope_id": body.envelope_id}


# ---------------------------------------------------------------------------
# Collab invite dispatch handlers
# ---------------------------------------------------------------------------


async def _handle_collab_invite(
    request: Request,
    contact_id: str,
    envelope: dict,
    body_data: dict,
) -> dict:
    """Handle an incoming collab_invite envelope — create a Decisions card
    so the local human can accept or decline the invitation.

    The envelope body is expected to contain:
      invite_id, project_id, project_name, project_slug, inviter,
      pin_required, display_name
    """
    decision_store = getattr(request.app.state, "decision_store", None)
    if decision_store is None:
        logger.warning(
            "peer_inbox: collab_invite received but decision_store not available"
        )
        return {"status": "received", "kind": "collab_invite", "dispatched": False}

    invite_id = body_data.get("invite_id", "unknown")
    project_name = body_data.get("project_name", "unknown project")
    inviter = body_data.get("inviter", contact_id)
    pin_required = body_data.get("pin_required", True)

    question = (
        f"{inviter} invites you to collaborate on project "
        f"\"{project_name}\" as a human member. "
        "You will appear in the members list, can chat in the project, "
        "and can later delegate agents to work on it."
    )

    options = [
        {"label": "Accept", "value": "accept", "recommended": True,
         "rationale": "Join the project as a collaborator"},
        {"label": "Decline", "value": "decline",
         "rationale": "Decline the invitation"},
    ]

    metadata: dict = {
        "envelope_kind": "collab_invite",
        "invite_id": invite_id,
        "inviter": inviter,
        "contact_id": contact_id,
    }
    for k in ("project_id", "project_slug", "pin_required", "display_name"):
        if k in body_data:
            metadata[k] = body_data[k]

    try:
        decision = await decision_store.create(
            from_agent=f"peer:{contact_id}",
            question=question,
            type="approve_deny",
            options=options,
            priority="blocking",
            metadata=metadata,
        )
        logger.info(
            "peer_inbox: collab_invite → decision %s created for contact=%s",
            decision["id"], contact_id,
        )
    except Exception as exc:
        logger.error(
            "peer_inbox: failed to create decision for collab_invite: %s", exc
        )
        return {
            "status": "received",
            "kind": "collab_invite",
            "dispatched": False,
            "error": str(exc),
        }

    return {
        "status": "received",
        "kind": "collab_invite",
        "decision_id": decision["id"],
        "dispatched": True,
    }


async def _handle_collab_response(
    request: Request,
    contact_id: str,
    envelope: dict,
    body_data: dict,
    kind: str,
) -> dict:
    """Handle a collab invite response envelope (accept or decline).

    On accept: add the contact as member_kind=\"human\" to the project.
    On decline: mark the invite as expired.

    The envelope body is expected to contain:
      invite_id, project_id, accepted (bool)
    """
    invite_id = body_data.get("invite_id", "")
    project_id = body_data.get("project_id", "")
    accepted = body_data.get("accepted", kind == "collab_invite_accept")

    if not invite_id or not project_id:
        logger.warning(
            "peer_inbox: %s missing invite_id or project_id from contact=%s",
            kind, contact_id,
        )
        return {"status": "received", "kind": kind, "dispatched": False}

    project_store = getattr(request.app.state, "project_store", None)
    invite_store = getattr(request.app.state, "project_invites", None)

    if accepted:
        # ---- Accept: add the contact as member_kind="human" ----
        if project_store is None:
            return {"status": "received", "kind": kind, "dispatched": False,
                    "error": "project_store not available"}

        # Verify the invite exists, is pending, and matches the project + contact.
        if invite_store is not None:
            invite = await invite_store.get(invite_id)
            if invite is None:
                return {"status": "received", "kind": kind, "dispatched": False,
                        "error": f"invite {invite_id} not found"}
            if invite.get("status") != "pending":
                return {"status": "received", "kind": kind, "dispatched": False,
                        "error": f"invite {invite_id} is not pending (status={invite.get('status')})"}
            if invite.get("project_id") != project_id:
                return {"status": "received", "kind": kind, "dispatched": False,
                        "error": f"invite {invite_id} project mismatch"}
            if invite.get("contact_id") != contact_id:
                return {"status": "received", "kind": kind, "dispatched": False,
                        "error": f"invite {invite_id} contact mismatch: "
                                 f"expected {invite.get('contact_id')}, got {contact_id}"}

        try:
            await project_store.add_member(
                project_id=project_id,
                member_id=contact_id,
                member_kind="human",
                role="member",
            )
            await project_store.log_activity(
                project_id, contact_id, "member.added",
                {"member_id": contact_id, "kind": "human", "via": "collab_invite"},
            )
            logger.info(
                "peer_inbox: %s → added %s as human member to project %s",
                kind, contact_id, project_id,
            )
        except Exception as exc:
            logger.error(
                "peer_inbox: failed to add member for %s: %s", kind, exc
            )
            return {
                "status": "received", "kind": kind, "dispatched": False,
                "error": str(exc),
            }

        # Mark the invite as redeemed.
        if invite_store is not None:
            try:
                await invite_store.mark_accepted(invite_id, contact_id)
            except Exception:
                pass  # audit best-effort
    else:
        # ---- Decline: mark invite as expired ----
        if invite_store is not None:
            try:
                await invite_store.mark_expired(invite_id)
            except Exception:
                pass  # audit best-effort
        logger.info(
            "peer_inbox: %s → invite %s declined by %s",
            kind, invite_id, contact_id,
        )

    return {"status": "received", "kind": kind, "dispatched": True}
