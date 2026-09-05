"""Hub identity keystore (hub social network slice 1).

The two minted keypairs (Ed25519 signing + X25519 encryption) are generated on
the node, persist 0600 under the data dir, and round-trip across restarts so the
author fingerprint is stable. The registration proof is a signature over a
server challenge; a proof signed by the wrong key must not verify.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from tinyagentos.hub import identity


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    return tmp_path


class TestKeystore:
    def test_keygen_on_first_use(self, data_dir):
        assert identity.exists() is False
        ident = identity.load_or_create()
        assert identity.exists() is True
        # Both keypairs minted: raw Ed25519 / X25519 keys are 32 bytes = 64 hex.
        for field in ("signing_private", "signing_public", "encryption_private", "encryption_public"):
            assert len(bytes.fromhex(ident[field])) == 32

    def test_round_trip_is_stable(self, data_dir):
        first = identity.load_or_create()
        # A second call must not re-mint: the identity (and its fingerprint) is
        # stable across restarts.
        second = identity.load_or_create()
        assert first["signing_public"] == second["signing_public"]
        assert first["signing_private"] == second["signing_private"]
        assert first["encryption_public"] == second["encryption_public"]

    def test_file_is_0600(self, data_dir):
        identity.load_or_create()
        mode = stat.S_IMODE(os.stat(data_dir / "hub" / "identity.json").st_mode)
        assert mode == 0o600

    def test_private_keys_persist_only_in_the_0600_file(self, data_dir):
        ident = identity.load_or_create()
        on_disk = json.loads((data_dir / "hub" / "identity.json").read_text())
        assert on_disk["signing_private"] == ident["signing_private"]
        # The registerable view never carries private material.
        pub = identity.public_identity()
        assert "signing_private" not in pub
        assert "encryption_private" not in pub

    def test_public_identity_shape(self, data_dir):
        ident = identity.load_or_create()
        pub = identity.public_identity()
        assert pub["signing_pubkey"] == ident["signing_public"]
        assert pub["encryption_pubkey"] == ident["encryption_public"]
        assert pub["fingerprint"] == identity.fingerprint(ident["signing_public"])

    def test_fingerprint_is_sha256_of_signing_pubkey(self, data_dir):
        import hashlib

        ident = identity.load_or_create()
        expected = hashlib.sha256(bytes.fromhex(ident["signing_public"])).hexdigest()
        assert identity.signing_fingerprint() == expected

    def test_corrupt_file_is_treated_as_absent(self, data_dir):
        (data_dir / "hub").mkdir(parents=True, exist_ok=True)
        (data_dir / "hub" / "identity.json").write_text("[1, 2, 3]")
        assert identity.exists() is False
        # load_or_create recovers by minting a fresh identity.
        ident = identity.load_or_create()
        assert ident["signing_public"]

    def test_clear(self, data_dir):
        identity.load_or_create()
        identity.clear()
        assert identity.exists() is False
        identity.clear()  # no-op when already gone

    def test_an_identity_persisted_mid_mint_is_adopted_not_clobbered(
        self, data_dir, monkeypatch
    ):
        """Concurrent first-boot minting must converge on one identity.

        Two processes sharing the data dir can both see no keystore and both
        mint. The write is atomic but it *replaces*, so the last one wins and
        the loser signs with a key that is not on disk — after a restart its
        author fingerprint changes and everything it signed is unverifiable
        against the identity the node actually has. Fault-injects that
        interleave by persisting a rival keystore while we are minting.
        """
        rival = identity.load_or_create()
        identity_file = data_dir / "hub" / "identity.json"
        rival_bytes = identity_file.read_bytes()
        identity_file.unlink()

        real_x25519 = identity.X25519PrivateKey

        class RacingX25519:
            @staticmethod
            def generate():
                if not identity_file.exists():
                    identity_file.parent.mkdir(parents=True, exist_ok=True)
                    identity_file.write_bytes(rival_bytes)
                return real_x25519.generate()

        monkeypatch.setattr(identity, "X25519PrivateKey", RacingX25519)

        minted = identity.load_or_create()

        on_disk = json.loads(identity_file.read_text())
        assert on_disk["signing_private"] == rival["signing_private"], (
            "an identity another process had already persisted was overwritten"
        )
        assert minted["signing_private"] == rival["signing_private"], (
            "returned an identity that is not the one on disk: this node would "
            "sign under a fingerprint it loses on the next restart"
        )


class TestChallengeProof:
    def test_registration_proof_verifies_with_the_right_key(self, data_dir):
        reg = identity.build_registration("server-challenge-abc")
        assert set(reg) == {"signing_pubkey", "encryption_pubkey", "proof"}
        assert identity.verify_signature(
            reg["signing_pubkey"], b"server-challenge-abc", reg["proof"]
        )

    def test_challenge_proof_rejects_a_wrong_key(self, data_dir, tmp_path, monkeypatch):
        reg = identity.build_registration("server-challenge-abc")
        # Mint a *different* identity in an isolated data dir and take its pubkey.
        other_dir = tmp_path / "other"
        monkeypatch.setenv("TAOS_DATA_DIR", str(other_dir))
        other = identity.load_or_create()
        wrong_pubkey = other["signing_public"]
        assert wrong_pubkey != reg["signing_pubkey"]
        # The proof was signed by the first key; verifying against the wrong key
        # must fail.
        assert (
            identity.verify_signature(wrong_pubkey, b"server-challenge-abc", reg["proof"])
            is False
        )

    def test_proof_rejects_tampered_challenge(self, data_dir):
        reg = identity.build_registration("server-challenge-abc")
        assert (
            identity.verify_signature(reg["signing_pubkey"], b"different", reg["proof"])
            is False
        )

    def test_verify_signature_never_raises_on_garbage(self, data_dir):
        assert identity.verify_signature("nothex", b"x", "alsonothex") is False
        assert identity.verify_signature("", b"x", "") is False


class TestSaveNewRepairsUnparsableReadback:
    def test_a_nul_filled_file_is_repaired_not_re_minted_every_boot(
        self, data_dir
    ) -> None:
        """``atomic_create_bytes`` can hand ``_save_new`` bytes it cannot parse
        when the file already at the path is pre-existing corruption (the
        2026-08-21 NUL-filled shape). If ``_save_new`` returns its in-memory
        creds without repairing the on-disk file, the bad bytes are still
        there on the next boot: ``_load`` sees them, treats them as absent,
        and mints again -- forever. One recovery cycle must end with a
        keystore that is actually usable on disk.
        """
        hub_dir = data_dir / "hub"
        hub_dir.mkdir(parents=True, exist_ok=True)
        (hub_dir / "identity.json").write_bytes(b"\x00" * 200)

        first = identity.load_or_create()
        second = identity.load_or_create()

        assert second["signing_private"] == first["signing_private"], (
            "load_or_create minted a different identity on the second call -- "
            "the corrupt file was never repaired on disk, so every boot mints "
            "and discards a fresh identity"
        )
