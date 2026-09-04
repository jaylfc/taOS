"""Ed25519-signed envelopes and peer-token auth for the peer channel.

Reuses ``tinyagentos.hub.identity`` for key material: the node's own Ed25519
signing key signs every outgoing envelope; incoming envelopes are verified
against the sender's pinned public key from the contacts store.

Peer tokens are minted by the owning instance and presented by the remote
instance as ``Authorization: Bearer <peer-token>`` on ``POST /api/peer/*``.
They are opaque hex strings, hashed at rest in the peer_links table (same
pattern as registry tokens).  The ``sub`` concept is ``contact:{username}``
and peer tokens grant *only* the peer route family — never the general API.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from pathlib import Path

from tinyagentos.hub.identity import (
    public_identity,
    sign as _sign,
    signing_fingerprint,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Signed envelopes
# ---------------------------------------------------------------------------

def build_envelope(
    *,
    from_username: str,
    to_username: str,
    kind: str,
    body: dict | None = None,
) -> dict:
    """Build a signed envelope from this node to a remote contact.

    ``kind`` is one of: ``handshake``, ``collab_invite``, ``delegation_request``,
    ``chat``, ``ack``.  ``body`` is the payload dict.

    The envelope is signed with this node's Ed25519 signing key (from
    ``hub.identity``), so the receiver can verify it against the pinned pubkey
    from the contacts store (trust-on-first-use at friend-accept).

    Envelope shape::

        {
            "from": "hub:jaylfc",
            "to":   "hub:hogne",
            "kind": "collab_invite",
            "body": { ... },
            "ts":   1710000000.0,
            "nonce": "hex...",
            "sig":   "hex..."
        }
    """
    import secrets

    nonce = secrets.token_hex(16)
    ts = time.time()
    envelope: dict = {
        "from": f"hub:{from_username}",
        "to": f"hub:{to_username}",
        "kind": kind,
        "ts": ts,
        "nonce": nonce,
    }
    if body is not None:
        envelope["body"] = body

    # Sign the canonical JSON representation (sorted keys, compact).
    payload = _canonical_json(envelope)
    sig = _sign(payload)
    envelope["sig"] = sig
    return envelope


def verify_envelope(
    envelope: dict,
    *,
    expected_kind: str | None = None,
    max_age_seconds: float = 300.0,
) -> tuple[bool, str]:
    """Verify a signed envelope against the sender's pinned Ed25519 pubkey.

    Returns ``(ok, error_reason)``.  ``ok`` is True only when all checks pass.
    The caller MUST have already resolved the sender's pubkey from the contacts
    store before calling this function.

    Checks performed:
    1. Required fields present (from, to, kind, ts, nonce, sig).
    2. Timestamp is within ``max_age_seconds`` of now (replay window).
    3. ``expected_kind`` matches if provided.
    (The caller must separately verify the Ed25519 signature against the
     sender's pinned pubkey via ``verify_envelope_signature``.)
    """
    required = ("from", "to", "kind", "ts", "nonce", "sig")
    for field in required:
        if field not in envelope:
            return False, f"missing required field: {field}"

    # Timestamp freshness: reject non-finite, future-only (past skew), and stale.
    ts = envelope["ts"]
    if not isinstance(ts, (int, float)) or not math.isfinite(ts):
        return False, f"non-finite timestamp: {ts!r}"
    now = time.time()
    age = now - ts  # positive = past, negative = future
    if age < -30.0:
        return False, f"envelope from the future: {abs(age):.0f}s ahead"
    if age > max_age_seconds + 30.0:
        return False, f"envelope too old: {age:.0f}s > {max_age_seconds}s"

    if expected_kind is not None and envelope["kind"] != expected_kind:
        return False, f"unexpected kind: {envelope['kind']} != {expected_kind}"

    return True, ""


def verify_envelope_signature(
    envelope: dict,
    sender_ed25519_pub: str,
) -> bool:
    """Verify ONLY the Ed25519 signature, assuming the caller has already checked
    freshness and structure. Returns True/False (never raises)."""
    sig = envelope.pop("sig", None)
    if sig is None:
        return False
    try:
        payload = _canonical_json(envelope)
        result = verify_signature(sender_ed25519_pub, payload, sig)
    finally:
        # Always restore the envelope dict.
        envelope["sig"] = sig
    return result


# ---------------------------------------------------------------------------
# Peer tokens
# ---------------------------------------------------------------------------

_PEER_TOKEN_BYTES = 32


def mint_peer_token(sub: str) -> tuple[str, str]:
    """Mint a new peer token for a contact.

    Returns ``(plaintext_token, token_hash)``.  The plaintext is given to the
    remote instance; the hash is stored in ``peer_links.inbound_token_hash``.

    ``sub`` is the token subject, e.g. ``"contact:hogne"``.

    Uses the same plain SHA-256 as ``ContactsStore._hash_token`` so that
    minting and verification are consistent.
    """
    import secrets

    raw = secrets.token_hex(_PEER_TOKEN_BYTES)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, token_hash


# ---------------------------------------------------------------------------
# Handshake delivery
# ---------------------------------------------------------------------------
# deliver_handshake POSTs to peer-supplied URLs.  Each target URL is passed
# through the shared SSRF guard (tinyagentos.routes.desktop_browser.ssrf)
# before the POST, so a peer endpoint can never reach loopback, link-local,
# multicast, CGNAT (100.64/10, where our own A2A bus lives), or other private
# ranges.  Endpoints may arrive as bare strings (send_handshake output) or as
# normalized dicts {"kind", "url", "priority"} (from _try_handshake in
# routes/hub.py, persisted via establish_peer_link); _endpoint_url handles
# both.  The first caller is A2's friend-accept flow in routes/hub.py.


def send_handshake(
    *,
    to_username: str,
    inbound_token: str,
    endpoints: list[str],
    signing_pubkey: str,
    encryption_pubkey: str,
) -> dict:
    """Build a handshake envelope addressed to a remote contact.

    The handshake envelope carries the inbound peer token (which the remote
    instance should present as ``Authorization: Bearer <token>`` when calling
    our ``POST /api/peer/*`` routes), our advertised endpoints, and our public
    keys so the remote side can pin them in its own contact row.

    Returns the envelope dict (not yet delivered).  The caller is responsible
    for delivering it to the peer's endpoints.
    """
    local_ident = public_identity()
    from_username = resolve_local_identity_id()
    if from_username is None:
        raise RuntimeError("cannot send handshake: no local hub identity")
    # Strip "hub:" prefix to get bare username
    bare_from = from_username.split(":", 1)[1] if from_username.startswith("hub:") else from_username

    body = {
        "inbound_token": inbound_token,
        "endpoints": endpoints,
        "signing_pubkey": signing_pubkey or local_ident.get("signing_pubkey", ""),
        "encryption_pubkey": encryption_pubkey or local_ident.get("encryption_pubkey", ""),
    }
    return build_envelope(
        from_username=bare_from,
        to_username=to_username,
        kind="handshake",
        body=body,
    )


def _endpoint_url(ep: str | dict) -> str | None:
    """Extract a URL string from a peer endpoint.

    Accepts both bare strings (legacy / send_handshake output) and the
    normalized dict form ``{"kind", "url", "priority"}`` produced by
    ``_try_handshake`` in routes/hub.py and persisted by
    ``establish_peer_link``.  Returns None for anything that is neither.
    """
    if isinstance(ep, dict):
        return ep.get("url")
    if isinstance(ep, str):
        return ep
    return None


async def deliver_handshake(
    envelope: dict,
    peer_endpoints: list[str | dict],
    *,
    http_client=None,
) -> bool:
    """Deliver a handshake envelope to the peer's endpoints (best-effort).

    Handles both bare-string endpoints and the normalized dict form
    ``{"kind", "url", "priority"}`` used by peer links (see
    ``_try_handshake`` in routes/hub.py and ``establish_peer_link``).

    Each target URL is validated against the shared SSRF guard before the
    POST so a peer-supplied pointer can never reach loopback, link-local,
    multicast, CGNAT (100.64/10, where our own A2A bus lives), or other
    private ranges.

    Tries each endpoint in order; stops on the first 2xx response.  Returns
    True if at least one endpoint accepted the envelope, False otherwise.

    ``http_client`` should be an ``httpx.AsyncClient``.  If None, a temporary
    client is created and torn down.
    """
    import httpx

    from tinyagentos.routes.desktop_browser.ssrf import (
        SsrfBlockedError,
        validate_url_or_raise,
    )

    own_client = http_client is None
    if own_client:
        http_client = httpx.AsyncClient(timeout=15.0)

    try:
        for ep in peer_endpoints:
            ep_url = _endpoint_url(ep)
            if not ep_url:
                continue
            url = ep_url.rstrip("/") + "/api/peer/inbox"
            try:
                validate_url_or_raise(url)
            except SsrfBlockedError:
                continue
            try:
                resp = await http_client.post(
                    url, json={"envelope": envelope},
                )
                if 200 <= resp.status_code < 300:
                    return True
            except Exception:
                continue
        return False
    finally:
        if own_client:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _canonical_json(obj: dict) -> bytes:
    """Canonical JSON: sorted keys, compact, UTF-8 bytes (no trailing newline)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def resolve_local_identity_id(data_dir: str | Path | None = None) -> str | None:
    """Return this node's local hub identity ID (``"hub:<username>"``), or None.

    Resolved from the hub identity keystore and the hub_authors table in
    hub.db.  Returns None if the node has not registered a hub identity
    (no identity keystore, or no matching author row).
    """
    try:
        fp = signing_fingerprint()
    except Exception:
        return None

    # Resolve hub.db path the same way hub.store resolves it:
    # TAOS_DATA_DIR override, else project data dir.
    if data_dir:
        hub_dir = Path(data_dir) / "hub"
    else:
        env = os.environ.get("TAOS_DATA_DIR")
        if env:
            hub_dir = Path(env) / "hub"
        else:
            hub_dir = Path(__file__).resolve().parent.parent / "data" / "hub"

    hub_db = hub_dir / "hub.db"
    if not hub_db.is_file():
        return None

    conn = None
    try:
        conn = sqlite3.connect(str(hub_db))
        row = conn.execute(
            "SELECT username FROM hub_authors WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        if row and row[0]:
            return f"hub:{row[0]}"
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        pass
    finally:
        if conn is not None:
            conn.close()
    return None
