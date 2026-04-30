"""
HMAC-signed tickets for shortcut redemption.

Replay protection (JtiTracker) is in-memory and process-local. This is
sufficient for single-process deployments (the v1 single-Pi case). For
cluster / multi-worker deployments, the tracker MUST be replaced with a
shared store (Redis is the obvious choice) — see follow-up issue.

The HMAC + 30s expiry already constrains the replay window to seconds.
The JTI check is the second line of defense within that window.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass


@dataclass
class Ticket:
    agent_id: str
    shortcut_idx: int
    scope: str
    exp: int          # unix seconds
    jti: str          # uuid4 hex, 32 chars
    worker_url: str


def _sign(payload: bytes, key: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def mint_ticket(
    agent_id: str,
    shortcut_idx: int,
    scope: str,
    signing_key: bytes,
    worker_url: str,
    ttl: int = 30,
) -> tuple[Ticket, str]:
    """Mint a signed ticket. Returns (Ticket, base64url-encoded token string)."""
    if len(signing_key) < 32:
        raise ValueError("signing_key must be at least 32 bytes")
    jti = uuid.uuid4().hex
    exp = int(time.time()) + ttl
    ticket = Ticket(
        agent_id=agent_id,
        shortcut_idx=shortcut_idx,
        scope=scope,
        exp=exp,
        jti=jti,
        worker_url=worker_url,
    )
    payload = json.dumps(
        {
            "agent_id": ticket.agent_id,
            "shortcut_idx": ticket.shortcut_idx,
            "scope": ticket.scope,
            "exp": ticket.exp,
            "jti": ticket.jti,
            "worker_url": ticket.worker_url,
        },
        separators=(",", ":"),
    ).encode()
    sig = _sign(payload, signing_key)
    token = base64.urlsafe_b64encode(payload + b"." + sig).decode()
    return ticket, token


def validate_ticket(
    token: str,
    signing_key: bytes,
    tracker: "JtiTracker",
) -> Ticket:
    """Validate a ticket token. Returns the Ticket on success.

    Raises ValueError with a descriptive message on any failure:
    - invalid signature
    - ticket expired
    - replayed jti
    """
    if len(signing_key) < 32:
        raise ValueError("signing_key must be at least 32 bytes")
    try:
        raw = base64.urlsafe_b64decode(token.encode() + b"=" * (-len(token) % 4))
        # HMAC-SHA256 is always 32 bytes; the separator is at the fixed offset
        # len(raw) - 33.  Using rfind would misfire if 0x2e appears in the sig.
        sig_start = len(raw) - 32
        if sig_start < 2 or raw[sig_start - 1:sig_start] != b".":
            raise ValueError("malformed token: no separator")
        payload_bytes = raw[:sig_start - 1]
        sig = raw[sig_start:]
    except Exception as exc:
        raise ValueError(f"invalid signature: token decode failed — {exc}") from exc

    expected_sig = _sign(payload_bytes, signing_key)
    if not hmac.compare_digest(sig, expected_sig):
        raise ValueError("invalid signature")

    try:
        data = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid signature: payload not JSON — {exc}") from exc

    if int(time.time()) > data["exp"]:
        raise ValueError("ticket expired")

    jti = data["jti"]
    if not tracker.record_if_new(jti, data["exp"]):
        raise ValueError("replayed jti")

    return Ticket(
        agent_id=data["agent_id"],
        shortcut_idx=data["shortcut_idx"],
        scope=data["scope"],
        exp=data["exp"],
        jti=jti,
        worker_url=data["worker_url"],
    )


class JtiTracker:
    """In-memory single-use JTI tracker. Thread-safe for a single process."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}  # jti -> exp
        self._lock = threading.Lock()

    def seen(self, jti: str) -> bool:
        """Return True if this jti has been recorded. Kept for test inspection."""
        with self._lock:
            self._evict()
            return jti in self._seen

    def record(self, jti: str, exp: int) -> None:
        """Mark jti as used. exp is the unix-second expiry of the ticket."""
        with self._lock:
            self._seen[jti] = exp

    def record_if_new(self, jti: str, exp: int) -> bool:
        """Atomically check + record. Returns True if newly recorded, False if already seen."""
        with self._lock:
            self._evict()
            if jti in self._seen:
                return False
            self._seen[jti] = exp
            return True

    def _evict(self) -> None:
        """Remove expired entries to bound memory use. Caller must hold _lock."""
        now = int(time.time())
        expired = [k for k, v in self._seen.items() if v < now]
        for k in expired:
            del self._seen[k]


# Module-level singleton. Routes import this for convenience so they don't
# need to manage the tracker's lifecycle.
_GLOBAL_JTI_TRACKER: JtiTracker = JtiTracker()
