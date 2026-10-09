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
from urllib.parse import urlparse

from tinyagentos.hub.identity import (
    public_identity,
    sign as _sign,
    signing_fingerprint,
    verify_signature,
)


# ---------------------------------------------------------------------------
# Signed envelopes
# ---------------------------------------------------------------------------

def local_contact_id() -> str:
    """This node's canonical peer identity: ``hub:<signing fingerprint>``.

    The same form ``routes/hub.py`` keys contact rows on at friend-accept, so
    the ``from`` and ``to`` of every envelope compare directly against
    ``contacts.contact_id``.  Reads the hub identity keystore (minting it on
    first use); no hub_authors row is involved.
    """
    return f"hub:{signing_fingerprint()}"


def build_envelope(
    *,
    to_contact_id: str,
    kind: str,
    body: dict | None = None,
) -> dict:
    """Build a signed envelope from this node to a remote contact.

    ``to_contact_id`` is the recipient's canonical ``hub:<fingerprint>`` id
    (the contacts row key); ``from`` is this node's :func:`local_contact_id`.

    ``kind`` is one of: ``handshake``, ``collab_invite``, ``delegation_request``,
    ``chat``, ``ack``.  ``body`` is the payload dict.

    The envelope is signed with this node's Ed25519 signing key (from
    ``hub.identity``), so the receiver can verify it against the pinned pubkey
    from the contacts store (trust-on-first-use at friend-accept).

    Envelope shape::

        {
            "from": "hub:<sender signing fingerprint>",
            "to":   "hub:<recipient signing fingerprint>",
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
        "from": local_contact_id(),
        "to": to_contact_id,
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
# deliver_handshake and deliver_to_peer POST to peer-supplied URLs.  Each
# target URL is passed through the shared SSRF guard
# (tinyagentos.routes.desktop_browser.ssrf) before the POST, so a peer
# endpoint can never reach loopback, link-local, multicast, or other private
# ranges.  The one allowance is the known-endpoint set: the exact (host, port)
# pairs RECORDED on the contact's peer link (known_hostports) may sit in a
# private or CGNAT range, because friends on the same tailnet live there.
# That set is only ever built from the stored link, never from an envelope
# or a request.  Endpoints may arrive as bare strings (send_handshake output)
# or as normalized dicts {"kind", "url", "priority"} (from _try_handshake in
# routes/hub.py, persisted via establish_peer_link); _endpoint_url handles
# both.  The first caller is A2's friend-accept flow in routes/hub.py.


def send_handshake(
    *,
    to_contact_id: str,
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

    body = {
        "inbound_token": inbound_token,
        "endpoints": endpoints,
        "signing_pubkey": signing_pubkey or local_ident.get("signing_pubkey", ""),
        "encryption_pubkey": encryption_pubkey or local_ident.get("encryption_pubkey", ""),
    }
    return build_envelope(
        to_contact_id=to_contact_id,
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


def known_hostports(endpoints: list[str | dict] | None) -> frozenset[tuple[str, int]]:
    """Exact ``(host, port)`` pairs of RECORDED peer endpoints.

    Feeds the SSRF guard's known-endpoint allowance.  Build it only from a
    stored peer link (``contacts_store.get_peer_link(...)["endpoints"]``), never
    from an envelope or an incoming request: the allowance exists so a friend's
    recorded tailnet address is reachable, not so a sender can name one.
    """
    from tinyagentos.routes.desktop_browser.ssrf import _hostport

    out: set[tuple[str, int]] = set()
    for ep in endpoints or []:
        ep_url = _endpoint_url(ep)
        if not ep_url:
            continue
        parsed = urlparse(ep_url)
        try:
            port = parsed.port
        except ValueError:
            continue
        if not parsed.hostname:
            continue
        out.add(_hostport(parsed.hostname, port, parsed.scheme))
    return frozenset(out)


def _endpoint_priority(ep: str | dict) -> int:
    if isinstance(ep, dict):
        try:
            return int(ep.get("priority", 99))
        except (TypeError, ValueError):
            return 99
    return 99


async def _post_envelope(
    endpoints: list[str | dict],
    path: str,
    envelope: dict,
    *,
    known: frozenset[tuple[str, int]],
    headers: dict | None = None,
    http_client=None,
    timeout: float = 15.0,
):
    """POST ``{"envelope": envelope}`` to ``<endpoint><path>``, first 2xx wins.

    Endpoints are tried in priority order.  Every URL is validated by the SSRF
    guard with the ``known`` allowance before the POST; a blocked or failing
    endpoint is skipped.  Returns the 2xx ``httpx.Response`` or None.

    ``http_client`` should be an ``httpx.AsyncClient``.  If None, a guarded
    client carrying the same allowance is created and torn down.
    """
    from tinyagentos.routes.desktop_browser.ssrf import (
        SsrfBlockedError,
        guarded_async_client,
        validate_url_or_raise,
    )

    own_client = http_client is None
    if own_client:
        # Guarded: the endpoint URL is peer-supplied, so the address that
        # passed the blocklist must be the address the POST connects to.
        http_client = guarded_async_client(timeout=timeout, allow_hostports=known)

    try:
        for ep in sorted(endpoints, key=_endpoint_priority):
            ep_url = _endpoint_url(ep)
            if not ep_url:
                continue
            url = ep_url.rstrip("/") + path
            try:
                validate_url_or_raise(url, allow_hostports=known)
            except SsrfBlockedError:
                continue
            try:
                resp = await http_client.post(
                    url, json={"envelope": envelope}, headers=headers or {},
                )
            except Exception:
                continue
            if 200 <= resp.status_code < 300:
                return resp
        return None
    finally:
        if own_client:
            await http_client.aclose()


async def deliver_handshake(
    envelope: dict,
    peer_endpoints: list[str | dict],
    *,
    http_client=None,
    known_endpoints: frozenset[tuple[str, int]] = frozenset(),
) -> dict | None:
    """Deliver a handshake envelope to the peer's endpoints (best-effort).

    POSTs to ``/api/peer/handshake`` on each endpoint in priority order and
    stops on the first 2xx.  Returns the peer's ``handshake_reply`` envelope
    as received, UNVERIFIED: the caller verifies it against the pinned key
    before trusting the token inside.  Returns None when no endpoint
    accepted the envelope.

    Each target URL is validated against the shared SSRF guard before the
    POST.  ``known_endpoints`` (from :func:`known_hostports` over the STORED
    peer link) is the only way a private or CGNAT address is reachable; an
    endpoint that is not recorded, or is recorded on another port, is refused
    without connecting.
    """
    resp = await _post_envelope(
        peer_endpoints, "/api/peer/handshake", envelope,
        known=known_endpoints, http_client=http_client,
    )
    if resp is None:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    reply = data.get("envelope") if isinstance(data, dict) else None
    return reply if isinstance(reply, dict) else None


async def deliver_to_peer(
    contacts_store,
    contact_id: str,
    path: str,
    envelope: dict,
    *,
    http_client=None,
    timeout: float = 10.0,
):
    """Deliver a signed envelope to an established contact over its peer link.

    Presents our outbound token (the one THEY minted for us) as the bearer and
    tries the link's recorded endpoints in priority order through the guarded
    client, with the link's own endpoints as the known-endpoint allowance.
    Returns the 2xx ``httpx.Response`` or None (no link, no endpoints, or
    every endpoint refused or failed).
    """
    link = await contacts_store.get_peer_link(contact_id)
    if link is None or not link.get("endpoints"):
        return None
    headers = {
        "Authorization": f"Bearer {link.get('outbound_token', '')}",
        "Content-Type": "application/json",
    }
    return await _post_envelope(
        link["endpoints"], path, envelope,
        known=known_hostports(link["endpoints"]),
        headers=headers, http_client=http_client, timeout=timeout,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _canonical_json(obj: dict) -> bytes:
    """Canonical JSON: sorted keys, compact, UTF-8 bytes (no trailing newline)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def resolve_local_identity_id(data_dir: str | Path | None = None) -> str | None:
    """Return this node's hub DIRECTORY identity (``"hub:<username>"``), or None.

    Username form: only the hub/relay recipient binding (routes/account_proxy.py)
    uses it.  Peer envelopes use :func:`local_contact_id` instead.

    Resolved from the hub identity keystore and the hub_authors table in
    hub.db.  Returns None if the node has not registered a hub identity
    (no identity keystore, or no matching author row).
    """
    try:
        fp = signing_fingerprint()
    except Exception:
        return None

    if data_dir:
        hub_dir = Path(data_dir) / "hub"
    else:
        from tinyagentos.app import resolve_data_dir
        hub_dir = resolve_data_dir() / "hub"

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
