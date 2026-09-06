"""Short-lived HMAC tickets for browser-proxy-origin redemption.

The browser proxy is served on a SECOND origin (a separate port) so the
proxied iframe can take ``allow-same-origin`` and register a service
worker without that SW being able to reach taOS APIs (there are none on
that origin). Because that origin is cross-origin to the taOS shell it
never receives the ``taos_session`` cookie, so it needs its own auth
gate — otherwise it would be an open web proxy.

The flow mirrors the existing ``/shortcut/`` redeem pattern
(:mod:`tinyagentos.shortcuts.tickets`):

  1. The MAIN app (:6969), behind the normal session gate, mints a
     short-lived signed ticket via :func:`mint_proxy_ticket`.
  2. The browser the user is driving redeems it at
     ``/__taos/redeem?ticket=...`` on the proxy origin (:6970), which
     validates it via :func:`validate_proxy_ticket`, sets the
     ``taos_browser`` cookie, and 302-redirects to the proxied URL.

The signing key is a per-process random secret shared between both ASGI
apps via ``app.state.browser_proxy_signing_key`` (both apps reference the
same state object — see :mod:`tinyagentos.browser_proxy_origin`). It is
regenerated on every restart, which simply invalidates outstanding
tickets and cookies; the user transparently re-redeems on next open.

Replay protection is the same in-memory single-use JTI tracker used by
shortcuts — sufficient for the single-process v1 deployment. The 30s
expiry already constrains the replay window to seconds.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from tinyagentos.shortcuts.tickets import JtiTracker

# Module-level single-use tracker. Process-local, like the shortcut one.
_PROXY_JTI_TRACKER: JtiTracker = JtiTracker()


@dataclass
class ProxyTicket:
    user_id: str
    exp: int  # unix seconds


def _sign(payload: bytes, key: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def mint_proxy_ticket(
    user_id: str,
    signing_key: bytes,
    ttl: int = 30,
) -> tuple[ProxyTicket, str]:
    """Mint a signed browser-proxy ticket.

    Returns ``(ProxyTicket, base64url-encoded token string)``.
    """
    if len(signing_key) < 32:
        raise ValueError("signing_key must be at least 32 bytes")
    jti = uuid.uuid4().hex
    exp = int(time.time()) + ttl
    payload = json.dumps(
        {"user_id": user_id, "exp": exp, "jti": jti},
        separators=(",", ":"),
    ).encode()
    sig = _sign(payload, signing_key)
    token = base64.urlsafe_b64encode(payload + b"." + sig).decode()
    return ProxyTicket(user_id=user_id, exp=exp), token


def validate_proxy_ticket(
    token: str,
    signing_key: bytes,
    tracker: JtiTracker = _PROXY_JTI_TRACKER,
) -> ProxyTicket:
    """Validate a browser-proxy ticket token. Returns the ticket on success.

    Raises ``ValueError`` on invalid signature, expiry, or replay.
    """
    if len(signing_key) < 32:
        raise ValueError("signing_key must be at least 32 bytes")
    try:
        raw = base64.urlsafe_b64decode(token.encode() + b"=" * (-len(token) % 4))
        # HMAC-SHA256 is always 32 bytes; the separator '.' sits at the
        # fixed offset len(raw) - 33 (mirrors shortcuts.tickets).
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

    return ProxyTicket(user_id=data["user_id"], exp=data["exp"])
