"""Hub relay — X25519 envelope sealing for store-and-forward through taos.my.

See ``docs/design/cross-user-collaboration.md`` (section 6 "Transport" and
section 7 "Human-to-human chat").  The hub relay carries *sealed* envelopes:
the outer envelope has only the recipient username (for routing) and the
sender's ephemeral X25519 public key; the inner payload is X25519-encrypted
with ChaCha20-Poly1305 so the hub never sees plaintext.

Envelope lifecycle
------------------
1. **Seal** — the sender encrypts the signed inner payload to the recipient's
   X25519 public key (ECDH + HKDF → ChaCha20-Poly1305), wraps it in an outer
   envelope, and drops it at the hub.
2. **Store** — the hub queues the outer envelope (SQLite, TTL 7 d, max 200
   per recipient).  The hub never decrypts; it only inspects ``recipient`` for
   routing and ``created_at`` for expiry.
3. **Poll** — the recipient's node polls the hub via the account proxy, gets
   the queued envelopes, and **unseals** each one with its own X25519 private
   key.  Verified envelopes are deleted from the hub; malformed ones are
   silently dropped.

Constraints (enforced at the hub, surfaced in the API)
- Max envelope size: 32 KB (outer envelope base64-encoded)
- TTL: 7 days (hub prunes expired envelopes on poll)
- Per-recipient cap: 200 queued envelopes (hub rejects when full)
"""
from __future__ import annotations

import base64
import json
import os
import time

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

# ---------------------------------------------------------------------------
# Constants (shared with the hub)
# ---------------------------------------------------------------------------

MAX_ENVELOPE_SIZE = 32 * 1024        # 32 KB
ENVELOPE_TTL_SECONDS = 7 * 24 * 3600  # 7 days
MAX_QUEUED_PER_RECIPIENT = 200

# HKDF info strings to domain-separate different key usages.
_HKDF_INFO_SEAL = b"taos-hub-relay-seal-v1"


# ---------------------------------------------------------------------------
# Low-level X25519 ECDH + HKDF → ChaCha20-Poly1305
# ---------------------------------------------------------------------------

def _derive_symmetric_key(
    ephemeral_private: X25519PrivateKey,
    recipient_public: X25519PublicKey,
) -> bytes:
    """ECDH + HKDF-SHA256 → 32-byte ChaCha20-Poly1305 key."""
    shared = ephemeral_private.exchange(recipient_public)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HKDF_INFO_SEAL,
    ).derive(shared)


# ---------------------------------------------------------------------------
# Outer envelope helpers
# ---------------------------------------------------------------------------

def _b64(data: bytes) -> str:
    """Standard base64 (no padding), URL-safe for transport."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64_decode(s: str) -> bytes:
    """Reverse of _b64 — tolerant of missing padding."""
    s = s.strip()
    s += "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def seal(
    *,
    plaintext: bytes,
    recipient_x25519_pub_hex: str,
) -> dict:
    """Encrypt *plaintext* to *recipient_x25519_pub_hex* and return an outer
    envelope suitable for ``POST /api/hub/relay/drop``.

    Returns a dict with keys ``sender_ephemeral_pub``, ``nonce``,
    ``ciphertext`` (all base64-encoded) plus a ``created_at`` timestamp.
    """
    recipient_pub = X25519PublicKey.from_public_bytes(
        bytes.fromhex(recipient_x25519_pub_hex)
    )
    ephemeral_priv = X25519PrivateKey.generate()
    ephemeral_pub_raw = ephemeral_priv.public_key().public_bytes_raw()
    key = _derive_symmetric_key(ephemeral_priv, recipient_pub)

    # ChaCha20-Poly1305 requires a 12-byte nonce.  Generate one per-message
    # (96 bits from os.urandom is safe for this AEAD).
    nonce = os.urandom(12)
    aead = ChaCha20Poly1305(key)
    ciphertext = aead.encrypt(nonce, plaintext, None)

    return {
        "sender_ephemeral_pub": _b64(ephemeral_pub_raw),
        "nonce": _b64(nonce),
        "ciphertext": _b64(ciphertext),
        "created_at": time.time(),
    }


def unseal(
    *,
    envelope: dict,
    recipient_x25519_priv_hex: str,
) -> bytes | None:
    """Decrypt a sealed envelope with the recipient's X25519 private key.

    Returns the plaintext bytes on success, ``None`` on any failure (wrong key,
    tampered ciphertext, malformed envelope).  This is deliberately fail-closed
    so a corrupted envelope is silently dropped rather than raising.
    """
    try:
        ephemeral_pub_raw = _b64_decode(envelope["sender_ephemeral_pub"])
        nonce = _b64_decode(envelope["nonce"])
        ciphertext = _b64_decode(envelope["ciphertext"])
        recipient_priv = X25519PrivateKey.from_private_bytes(
            bytes.fromhex(recipient_x25519_priv_hex)
        )
        ephemeral_pub = X25519PublicKey.from_public_bytes(ephemeral_pub_raw)
        key = _derive_symmetric_key(recipient_priv, ephemeral_pub)
        aead = ChaCha20Poly1305(key)
        return aead.decrypt(nonce, ciphertext, None)
    except Exception:
        return None


def build_envelope(
    *,
    recipient: str,
    recipient_x25519_pub_hex: str,
    inner_payload: bytes,
) -> dict:
    """Seal *inner_payload* and return the complete outer envelope for hub drop.

    ``recipient`` is the hub username (e.g. ``"hub:hogne"``) — the only field
    the hub inspects.  The inner payload is the signed canonical-JSON object
    that the recipient verifies after unsealing.
    """
    outer = seal(plaintext=inner_payload,
                 recipient_x25519_pub_hex=recipient_x25519_pub_hex)
    outer["recipient"] = recipient
    # Enforce max size on the base64-encoded wire form (the hub receives the
    # envelope as JSON; base64 inflation means a 32 KB raw envelope is ~43 KB
    # on the wire, so we measure the encoded form for an honest transport cap).
    raw = json.dumps(outer, sort_keys=True, separators=(",", ":"))
    raw_bytes = raw.encode("utf-8")
    b64_encoded = base64.urlsafe_b64encode(raw_bytes)
    if len(b64_encoded) > MAX_ENVELOPE_SIZE:
        raise ValueError(
            f"sealed envelope is {len(b64_encoded)} bytes base64-encoded, "
            f"exceeds {MAX_ENVELOPE_SIZE} byte limit"
        )
    return outer


# ---------------------------------------------------------------------------
# Inner signed-envelope helpers (Ed25519 signing done by callers)
# ---------------------------------------------------------------------------


def canonicalize(payload: dict) -> bytes:
    """Canonical JSON bytes of *payload*, excluding any ``sig`` field.

    Used by callers to produce the bytes that are Ed25519-signed (design doc
    section 2, "Auth story for the peer channel").  The same bytes are fed to
    :func:`seal` so the recipient can verify the signature after unsealing.
    """
    inner = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(
        inner, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
