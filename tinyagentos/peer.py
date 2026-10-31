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
    ``delegation_status``, ``chat``, ``ack``.  ``body`` is the payload dict.

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
# Every POST to a peer goes through the shared SSRF guard
# (tinyagentos.routes.desktop_browser.ssrf): the URL is validated first and
# the default client pins the connection to the checked address.  Loopback,
# link-local, multicast, reserved and unspecified are always refused.
# Private and CGNAT (100.64/10, where our own A2A bus lives) addresses are
# refused too, EXCEPT an exact (host, port) recorded on the contact's own
# peer link (``known_endpoints``, built by :func:`known_hostports`).  That set
# is only ever built from the local contacts store, never from an envelope or
# a request.  Endpoints may arrive as bare strings (send_handshake output) or
# as normalized dicts {"kind", "url", "priority"} (from _try_handshake in
# routes/hub.py, persisted via establish_peer_link); _endpoint_url handles
# both.

HANDSHAKE_PATH = "/api/peer/handshake"


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
    """Exact ``(host, port)`` pairs of a peer link's recorded endpoints.

    Pass ``contacts_store.get_peer_link(contact_id)["endpoints"]``; never
    endpoints taken from an envelope or a request.
    """
    from urllib.parse import urlparse

    from tinyagentos.routes.desktop_browser.ssrf import url_hostport

    pairs = set()
    for ep in endpoints or []:
        url = _endpoint_url(ep)
        if not isinstance(url, str) or not url:
            continue
        hp = url_hostport(urlparse(url))
        if hp is not None:
            pairs.add(hp)
    return frozenset(pairs)


async def _post_to_endpoints(
    peer_endpoints: list[str | dict],
    path: str,
    payload: dict,
    *,
    known_endpoints: frozenset[tuple[str, int]] = frozenset(),
    http_client=None,
    headers: dict | None = None,
    timeout: float = 15.0,
):
    """POST ``payload`` to ``<endpoint><path>`` for each endpoint in order.

    Returns the first 2xx ``httpx.Response``, or None.  A URL the SSRF guard
    refuses is skipped without connecting.
    """
    from tinyagentos.routes.desktop_browser import ssrf

    own_client = http_client is None
    if own_client:
        # Guarded: the address that passed the blocklist must be the address
        # the POST connects to.
        http_client = ssrf.guarded_async_client(
            allow_hostports=known_endpoints, timeout=timeout,
        )

    try:
        for ep in peer_endpoints:
            ep_url = _endpoint_url(ep)
            if not ep_url:
                continue
            url = ep_url.rstrip("/") + path
            try:
                ssrf.validate_url_or_raise(url, allow_hostports=known_endpoints)
            except ssrf.SsrfBlockedError:
                continue
            try:
                resp = await http_client.post(url, json=payload, headers=headers)
                if 200 <= resp.status_code < 300:
                    return resp
            except Exception:
                continue
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
) -> bool:
    """Deliver a handshake envelope to the peer's ``POST /api/peer/handshake``.

    Tries each endpoint in order; stops on the first 2xx response.  Returns
    True if at least one endpoint accepted the envelope, False otherwise.

    ``known_endpoints`` comes from :func:`known_hostports` over the contact's
    stored peer link; only those exact ``(host, port)`` pairs may be private
    or CGNAT.  ``http_client`` should be an ``httpx.AsyncClient``.  If None,
    a guarded client is created and torn down.
    """
    resp = await _post_to_endpoints(
        peer_endpoints, HANDSHAKE_PATH, {"envelope": envelope},
        known_endpoints=known_endpoints, http_client=http_client,
    )
    return resp is not None


async def exchange_handshake(
    envelope: dict,
    peer_endpoints: list[str | dict],
    *,
    http_client=None,
    known_endpoints: frozenset[tuple[str, int]] = frozenset(),
) -> dict | None:
    """Deliver a handshake like :func:`deliver_handshake`; return the reply envelope.

    The reply is unverified: pass it to :func:`verify_handshake_reply`.
    Returns None when nothing accepted the handshake or the body has no
    envelope.
    """
    resp = await _post_to_endpoints(
        peer_endpoints, HANDSHAKE_PATH, {"envelope": envelope},
        known_endpoints=known_endpoints, http_client=http_client,
    )
    if resp is None:
        return None
    try:
        reply = resp.json().get("envelope")
    except (ValueError, AttributeError):
        return None
    return reply if isinstance(reply, dict) else None


def verify_handshake_reply(
    reply: dict | None,
    *,
    handshake: dict,
    sender_ed25519_pub: str,
) -> str | None:
    """Return the token the peer minted for us, or None if the reply fails.

    The reply must be a fresh ``handshake_reply`` from the contact we sent
    ``handshake`` to, addressed to us, answering that handshake's nonce, and
    signed by the key we pinned for them.
    """
    if not isinstance(reply, dict):
        return None
    ok, _err = verify_envelope(reply, expected_kind="handshake_reply")
    if not ok:
        return None
    if reply.get("from") != handshake.get("to"):
        return None
    if reply.get("to") != local_contact_id():
        return None
    if not verify_envelope_signature(reply, sender_ed25519_pub):
        return None
    body = reply.get("body")
    if not isinstance(body, dict) or body.get("reply_to") != handshake.get("nonce"):
        return None
    token = body.get("inbound_token")
    if not isinstance(token, str) or not token:
        return None
    return token


async def deliver_to_peer(
    contacts_store,
    contact_id: str,
    path: str,
    envelope: dict,
    *,
    http_client=None,
) -> bool:
    """POST ``envelope`` to a contact's ``path`` with our outbound bearer.

    Endpoints, the outbound token, and the known-endpoint SSRF allowance all
    come from the contact's stored peer link.  Endpoints are tried in
    priority order.  Returns True on the first 2xx.
    """
    link = await contacts_store.get_peer_link(contact_id)
    if link is None or not link.get("endpoints"):
        return False
    token = link.get("outbound_token") or ""
    if not token:
        # Handshake never completed: the peer would refuse an empty bearer.
        return False
    endpoints = sorted(
        link["endpoints"],
        key=lambda e: e.get("priority", 99) if isinstance(e, dict) else 99,
    )
    resp = await _post_to_endpoints(
        endpoints, path, {"envelope": envelope},
        known_endpoints=known_hostports(link["endpoints"]),
        http_client=http_client,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    return resp is not None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Outbound delivery
# ---------------------------------------------------------------------------

async def send_envelope(
    contacts_store,
    *,
    to_contact_id: str,
    kind: str,
    body: dict | None = None,
) -> tuple[bool, str]:
    """Build a signed envelope and deliver it to a remote contact's peer inbox.

    Returns ``(delivered, error)``.  ``delivered`` is True when the envelope
    was accepted (2xx) by at least one remote endpoint.  ``error`` is an empty
    string on success, otherwise a human-readable reason.

    This is the reusable outbound half of the peer channel — the inbound half
    is ``POST /api/peer/inbox`` in ``routes/peer.py``.

    ``to_contact_id`` is the recipient's canonical ``hub:<fingerprint>`` id
    (the contacts row key).  The envelope's ``from`` is this node's
    :func:`local_contact_id` — every peer envelope carries the fingerprint
    identity since tsk-abwk4t, never the ``hub:<username>`` directory form.
    """
    # ``to_contact_id`` must be the full ``hub:<...>`` contact id (the key
    # the contacts store uses).  ``build_envelope`` stamps it into ``to``
    # as-is — passing a bare id here would address the envelope to something
    # that never matches the remote node.  Fail loudly on a wrong shape
    # rather than silently producing an undeliverable envelope.
    if not to_contact_id.startswith("hub:"):
        raise ValueError(
            f"to_contact_id must be a 'hub:<...>' id, got {to_contact_id!r}"
        )
    envelope = build_envelope(
        to_contact_id=to_contact_id,
        kind=kind,
        body=body,
    )

    peer_link = await contacts_store.get_peer_link(to_contact_id)
    if peer_link is None or not peer_link.get("endpoints"):
        return False, "no peer endpoints for contact"

    import httpx

    outbound_token = peer_link.get("outbound_token", "")
    # Endpoints are plain URL strings in declaration order (``establish_peer_link``
    # stores ``list[str]``; JSON preserves insertion order, so the primary
    # endpoint is first).  For forward/backward compatibility we also accept
    # ``{"url": ..., "priority": ...}`` dicts and sort those by priority (lowest
    # first) so a declared-primary endpoint is tried before a fallback.  The
    # sort is stable, so string endpoints keep their insertion order.
    endpoints = peer_link["endpoints"]

    def _endpoint_sort_key(ep) -> tuple:
        if isinstance(ep, dict):
            return (0, ep.get("priority", 99))
        return (0, 99)

    for ep in sorted(endpoints, key=_endpoint_sort_key):
        ep_url = ep.get("url", "") if isinstance(ep, dict) else str(ep)
        inbox_url = f"{ep_url.rstrip('/')}/api/peer/inbox"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    inbox_url,
                    json={"envelope": envelope},
                    headers={
                        "Authorization": f"Bearer {outbound_token}",
                        "Content-Type": "application/json",
                    },
                )
                if 200 <= resp.status_code < 300:
                    return True, ""
        except Exception:
            continue
    return False, "failed to deliver envelope to any peer endpoint"


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
