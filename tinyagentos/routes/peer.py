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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from tinyagentos.peer import (
    resolve_local_identity_id,
    verify_envelope,
    verify_envelope_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/peer", tags=["peer"])
# IMPORTANT: every route under this prefix MUST call _authenticate_peer.
# Do not add a route here without bearer-auth — this is an instance-to-instance
# surface facing other nodes, not an end-user API.

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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/inbox")
async def peer_inbox(body: PeerEnvelope, request: Request):
    """Receive a signed envelope from a remote contact.

    The envelope body is verified against the sender's pinned Ed25519 pubkey
    (from the contacts store).  Rate-limited per contact.
    """
    contact_id = await _authenticate_peer(request)
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

    # Log the envelope kind for debugging; the real dispatch (collab invites,
    # chat messages, etc.) happens in later milestones.
    kind = envelope.get("kind", "unknown")
    logger.info(
        "peer_inbox: contact=%s kind=%s nonce=%s",
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
    contact_id = await _authenticate_peer(request)
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
    contact_id = await _authenticate_peer(request)
    store = request.app.state.contacts_store

    # The ack must come from the contact named in the body.
    if body.contact_id != contact_id:
        raise HTTPException(
            status_code=400,
            detail=f"contact_id {body.contact_id!r} does not match authenticated contact {contact_id!r}",
        )

    if not _rate_limit_ok(contact_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    await store.mark_peer_seen(contact_id)

    logger.info(
        "peer_ack: contact=%s envelope_nonce=%s",
        contact_id, body.envelope_id,
    )

    return {"status": "acked", "envelope_id": body.envelope_id}
