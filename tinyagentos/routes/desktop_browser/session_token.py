"""Short-lived HMAC tokens scoped to a single browser session.

Each token binds a ``session_id`` to a ``user_id`` so it grants access to
exactly one browser room.  The scheme mirrors
:mod:`tinyagentos.routes.desktop_browser.proxy_ticket` (HMAC-SHA256 +
base64url, same ``payload + "." + sig`` framing) and reuses the same
:class:`~tinyagentos.shortcuts.tickets.JtiTracker` for replay protection.

Typical flow:

  1. The main app (:6969) calls :func:`mint_session_token` when a browser
     session is created and returns the token to the client.
  2. The client presents the token on the stream endpoint; the endpoint
     calls :func:`validate_session_token` and admits only the matching
     session.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from tinyagentos.shortcuts.tickets import JtiTracker

# Module-level single-use tracker. Process-local, mirrors _PROXY_JTI_TRACKER.
_SESSION_JTI_TRACKER: JtiTracker = JtiTracker()


def _sign(payload: bytes, key: bytes) -> bytes:
    return hmac.new(key, payload, hashlib.sha256).digest()


def mint_session_token(
    session_id: str,
    user_id: str,
    signing_key: bytes,
    ttl: int = 60,
) -> tuple[dict, str]:
    """Mint a signed session token.

    Returns ``({"session_id": ..., "user_id": ..., "exp": ...}, token_string)``.
    The ``jti`` is embedded in the token but not exposed in the returned dict.
    """
    if len(signing_key) < 32:
        raise ValueError("signing_key must be at least 32 bytes")
    jti = uuid.uuid4().hex
    exp = int(time.time()) + ttl
    payload = json.dumps(
        {"session_id": session_id, "user_id": user_id, "exp": exp, "jti": jti},
        separators=(",", ":"),
    ).encode()
    sig = _sign(payload, signing_key)
    token = base64.urlsafe_b64encode(payload + b"." + sig).decode()
    return {"session_id": session_id, "user_id": user_id, "exp": exp}, token


def validate_session_token(
    token: str,
    signing_key: bytes,
    tracker: JtiTracker = _SESSION_JTI_TRACKER,
) -> dict:
    """Validate a session token. Returns ``{"session_id": ..., "user_id": ...}`` on success.

    Raises ``ValueError`` on invalid signature, expiry, or replay.
    """
    if len(signing_key) < 32:
        raise ValueError("signing_key must be at least 32 bytes")
    try:
        raw = base64.urlsafe_b64decode(token.encode() + b"=" * (-len(token) % 4))
        # HMAC-SHA256 is always 32 bytes; separator '.' sits at len(raw) - 33.
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
        raise ValueError("token expired")

    jti = data["jti"]
    if not tracker.record_if_new(jti, data["exp"]):
        raise ValueError("replayed jti")

    return {"session_id": data["session_id"], "user_id": data["user_id"]}
