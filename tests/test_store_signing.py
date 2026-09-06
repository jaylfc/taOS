"""Unit tests for tinyagentos/store_signing.py."""

from __future__ import annotations

import tempfile
from pathlib import Path

from tinyagentos.store_signing import (
    generate_signing_keypair,
    load_or_create_signing_keypair,
    sign_manifest,
    verify_manifest_signature,
)


class TestGenerateKeypair:
    def test_generates_valid_keypair(self):
        priv, pub = generate_signing_keypair()
        assert len(priv) > 0
        assert len(pub) > 0
        assert priv.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert pub.startswith(b"-----BEGIN PUBLIC KEY-----")

    def test_keypair_can_sign_and_verify(self):
        priv, pub = generate_signing_keypair()
        manifest = {"id": "test", "name": "Test", "type": "model", "version": "1.0.0"}
        sig = sign_manifest(manifest, priv)
        assert len(sig) == 128  # Ed25519 signature is 64 bytes → 128 hex chars
        assert verify_manifest_signature(manifest, sig, pub)


class TestSignAndVerify:
    def setup_method(self):
        self.priv, self.pub = generate_signing_keypair()
        self.manifest = {
            "id": "ollama",
            "name": "Ollama",
            "type": "service",
            "version": "latest",
            "install": {"method": "script"},
        }

    def test_verify_valid_signature(self):
        sig = sign_manifest(self.manifest, self.priv)
        assert verify_manifest_signature(self.manifest, sig, self.pub)

    def test_verify_tampered_signature(self):
        sig = sign_manifest(self.manifest, self.priv)
        # Flip the last byte so it ALWAYS differs from the original.
        # sig[:-2]+"ff" fails ~1/256 of the time when the last byte
        # already happens to be "ff".
        last_byte = int(sig[-2:], 16) ^ 0x01
        bad_sig = sig[:-2] + f"{last_byte:02x}"
        assert not verify_manifest_signature(self.manifest, bad_sig, self.pub)

    def test_verify_tampered_manifest(self):
        sig = sign_manifest(self.manifest, self.priv)
        tampered = {**self.manifest, "name": "Evil Ollama"}
        assert not verify_manifest_signature(tampered, sig, self.pub)

    def test_verify_empty_signature(self):
        assert not verify_manifest_signature(self.manifest, "", self.pub)

    def test_verify_wrong_key(self):
        sig = sign_manifest(self.manifest, self.priv)
        _, other_pub = generate_signing_keypair()
        assert not verify_manifest_signature(self.manifest, sig, other_pub)

    def test_signature_is_deterministic_for_same_input(self):
        """Ed25519 is deterministic — same input + key = same signature."""
        sig1 = sign_manifest(self.manifest, self.priv)
        sig2 = sign_manifest(self.manifest, self.priv)
        assert sig1 == sig2

    def test_different_manifests_produce_different_signatures(self):
        sig1 = sign_manifest(self.manifest, self.priv)
        sig2 = sign_manifest({**self.manifest, "id": "other"}, self.priv)
        assert sig1 != sig2

    def test_signature_field_stripped(self):
        """The _signature field is stripped before signing so embedding it
        doesn't create a circular dependency."""
        manifest_with_sig = {**self.manifest, "_signature": "should-be-ignored"}
        sig = sign_manifest(manifest_with_sig, self.priv)
        # Verify against the same manifest (with _signature field still there)
        assert verify_manifest_signature(manifest_with_sig, sig, self.pub)
        # Verify against clean manifest
        assert verify_manifest_signature(self.manifest, sig, self.pub)


class TestLoadOrCreateKeypair:
    def test_creates_keypair_on_first_call(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            priv, pub = load_or_create_signing_keypair(td)
            assert (td / "store_signing_key.json").exists()
            assert len(priv) > 0
            assert len(pub) > 0

    def test_returns_same_keypair_on_second_call(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            priv1, pub1 = load_or_create_signing_keypair(td)
            priv2, pub2 = load_or_create_signing_keypair(td)
            assert priv1 == priv2
            assert pub1 == pub2

    def test_file_permissions_are_restrictive(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            load_or_create_signing_keypair(td)
            keyfile = td / "store_signing_key.json"
            stat = keyfile.stat()
            # 0o600 = owner read+write only
            assert (stat.st_mode & 0o777) == 0o600
