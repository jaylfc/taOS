"""Tests for the hub sealed-envelope relay (cross-user collab A3)."""
from __future__ import annotations

import json
import os
import pytest

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
)

from tinyagentos.hub.relay import (
    canonicalize,
    seal,
    unseal,
    build_envelope,
    MAX_ENVELOPE_SIZE,
    _b64,
    _b64_decode,
)

# ---------------------------------------------------------------------------
# Round-trip: seal then unseal
# ---------------------------------------------------------------------------


def _make_x25519_keypair() -> tuple[str, str]:
    """Return (priv_hex, pub_hex) for a fresh X25519 keypair."""
    priv = X25519PrivateKey.generate()
    pub = priv.public_key()
    return (
        priv.private_bytes_raw().hex(),
        pub.public_bytes_raw().hex(),
    )


class TestRoundTrip:
    """A sealed envelope must survive seal→unseal across a real keypair."""

    def test_small_payload(self):
        priv_hex, pub_hex = _make_x25519_keypair()
        plaintext = b"hello sealed world"
        envelope = seal(plaintext=plaintext,
                        recipient_x25519_pub_hex=pub_hex)
        assert "sender_ephemeral_pub" in envelope
        assert "nonce" in envelope
        assert "ciphertext" in envelope
        result = unseal(envelope=envelope,
                        recipient_x25519_priv_hex=priv_hex)
        assert result == plaintext

    def test_larger_payload(self):
        priv_hex, pub_hex = _make_x25519_keypair()
        plaintext = os.urandom(4096)
        envelope = seal(plaintext=plaintext,
                        recipient_x25519_pub_hex=pub_hex)
        result = unseal(envelope=envelope,
                        recipient_x25519_priv_hex=priv_hex)
        assert result == plaintext

    def test_wrong_recipient_fails(self):
        alice_priv, alice_pub = _make_x25519_keypair()
        _, bob_pub = _make_x25519_keypair()
        envelope = seal(plaintext=b"secret",
                        recipient_x25519_pub_hex=alice_pub)
        # Bob tries to decrypt: should return None.
        bob_priv, _ = _make_x25519_keypair()
        result = unseal(envelope=envelope,
                        recipient_x25519_priv_hex=bob_priv)
        assert result is None

    def test_tampered_ciphertext_fails(self):
        priv_hex, pub_hex = _make_x25519_keypair()
        envelope = seal(plaintext=b"tamper me",
                        recipient_x25519_pub_hex=pub_hex)
        # Flip a byte in the ciphertext.
        ct = bytearray(_b64_decode(envelope["ciphertext"]))
        ct[0] ^= 0xFF
        envelope["ciphertext"] = _b64(bytes(ct))
        result = unseal(envelope=envelope,
                        recipient_x25519_priv_hex=priv_hex)
        assert result is None

    def test_unicode_plaintext(self):
        priv_hex, pub_hex = _make_x25519_keypair()
        plaintext = "héllo wörld 🔐".encode("utf-8")
        envelope = seal(plaintext=plaintext,
                        recipient_x25519_pub_hex=pub_hex)
        result = unseal(envelope=envelope,
                        recipient_x25519_priv_hex=priv_hex)
        assert result == plaintext


# ---------------------------------------------------------------------------
# build_envelope
# ---------------------------------------------------------------------------

class TestBuildEnvelope:
    def test_includes_recipient(self):
        _, pub_hex = _make_x25519_keypair()
        outer = build_envelope(
            recipient="hub:alice",
            recipient_x25519_pub_hex=pub_hex,
            inner_payload=b'{"type":"test"}',
        )
        assert outer["recipient"] == "hub:alice"
        assert "sender_ephemeral_pub" in outer
        assert "nonce" in outer
        assert "ciphertext" in outer
        assert "created_at" in outer

    def test_enforces_size_limit(self):
        _, pub_hex = _make_x25519_keypair()
        # Build a payload just over the limit (after base64 overhead).
        large = b"x" * (MAX_ENVELOPE_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds"):
            build_envelope(
                recipient="hub:alice",
                recipient_x25519_pub_hex=pub_hex,
                inner_payload=large,
            )

    def test_size_limit_measured_on_base64_not_raw_json(self):
        """The 32 KB limit is enforced on the base64-encoded wire form
        (docstring line 23), not the raw JSON.  Base64 inflates ~33 %,
        so an envelope whose raw JSON is under 32 KB but whose
        base64-encoded form exceeds it must be rejected."""
        _, pub_hex = _make_x25519_keypair()
        # Build a payload that produces a raw JSON just under the limit
        # but a base64-encoded form well over it.  The ciphertext is
        # base64-encoded inside the JSON, and then the whole JSON is
        # base64-encoded for the size check — so the effective wire cap
        # is reached at ~24 KB of plaintext (after AEAD tag + JSON
        # envelope overhead + outer base64 inflation).
        borderline = b"y" * 24000
        with pytest.raises(ValueError, match="base64-encoded"):
            build_envelope(
                recipient="hub:alice",
                recipient_x25519_pub_hex=pub_hex,
                inner_payload=borderline,
            )

    def test_small_envelope_passes_size_check(self):
        """A small payload must pass the base64-encoded size check."""
        _, pub_hex = _make_x25519_keypair()
        outer = build_envelope(
            recipient="hub:alice",
            recipient_x25519_pub_hex=pub_hex,
            inner_payload=b"tiny",
        )
        assert outer["recipient"] == "hub:alice"

    def test_inner_json_preserved_after_roundtrip(self):
        priv_hex, pub_hex = _make_x25519_keypair()
        inner = json.dumps(
            {"from": "hub:alice", "to": "hub:bob", "kind": "chat",
             "body": "hello"}
        ).encode("utf-8")
        outer = build_envelope(
            recipient="hub:bob",
            recipient_x25519_pub_hex=pub_hex,
            inner_payload=inner,
        )
        result = unseal(envelope=outer,
                        recipient_x25519_priv_hex=priv_hex)
        assert json.loads(result) == json.loads(inner)


# ---------------------------------------------------------------------------
# canonicalize
# ---------------------------------------------------------------------------

class TestCanonicalize:
    def test_strips_sig(self):
        payload = {"from": "a", "to": "b", "sig": "deadbeef"}
        c = canonicalize(payload)
        decoded = json.loads(c)
        assert "sig" not in decoded
        assert decoded["from"] == "a"

    def test_sorted_keys(self):
        payload = {"z": 1, "a": 2, "m": 3}
        c = canonicalize(payload)
        # canonical JSON: sorted keys, compact
        assert c == b'{"a":2,"m":3,"z":1}'

    def test_ensure_ascii_false_preserves_unicode(self):
        payload = {"text": "café"}
        c = canonicalize(payload)
        assert "café".encode("utf-8") in c

    def test_rejects_nan(self):
        """NaN is not valid JSON — canonicalize() must reject it."""
        import math
        with pytest.raises(ValueError):
            canonicalize({"v": float("nan")})

    def test_rejects_infinity(self):
        """Infinity is not valid JSON — canonicalize() must reject it."""
        with pytest.raises(ValueError):
            canonicalize({"v": float("inf")})


# ---------------------------------------------------------------------------
# base64 round-trip
# ---------------------------------------------------------------------------

class TestBase64:
    def test_roundtrip(self):
        data = os.urandom(32)
        assert _b64_decode(_b64(data)) == data

    def test_url_safe(self):
        # All-zero bytes produce '/' in standard base64 but '_' in urlsafe.
        data = bytes([0xFF, 0xFF])
        enc = _b64(data)
        assert "/" not in enc
        assert "+" not in enc
        assert "=" not in enc  # padding stripped
