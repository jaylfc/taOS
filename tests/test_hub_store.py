"""Local hub object store + canonical helpers (hub social slice 2).

Covers the four things slice 2 in ``docs/design/hub-social-network-foundation.md``
calls out: canonicalization vectors (the same object always serialises the same
way, key order irrelevant), sign/verify (a good signature verifies, tampering or
a wrong key does not), version-wins (a stale profile never clobbers a newer one),
and store round-trip (objects and blobs come back byte-for-byte).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.hub import identity
from tinyagentos.hub import store as hub_store


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    # Colocate the identity keystore and hub store under an isolated dir, exactly
    # as production resolves them from TAOS_DATA_DIR.
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest_asyncio.fixture
async def store(data_dir):
    s = hub_store.HubStore(hub_store.default_db_path())
    await s.init()
    try:
        yield s
    finally:
        await s.close()


class TestCanonicalization:
    def test_key_order_does_not_change_bytes(self):
        a = {"type": "profile", "author": "aa", "version": 1}
        b = {"version": 1, "author": "aa", "type": "profile"}
        assert hub_store.canonical_bytes(a) == hub_store.canonical_bytes(b)

    def test_canonical_json_is_sorted_and_compact(self):
        obj = {"b": 1, "a": 2}
        assert hub_store.canonical_json(obj) == '{"a":2,"b":1}'

    def test_sig_is_excluded_from_canonical_bytes(self):
        # The signature must not be part of what is signed/hashed, so adding it
        # cannot change the content address.
        base = {"type": "post", "author": "aa", "seq": 1}
        with_sig = {**base, "sig": "deadbeef"}
        assert hub_store.canonical_bytes(base) == hub_store.canonical_bytes(with_sig)
        assert hub_store.object_hash(base) == hub_store.object_hash(with_sig)

    def test_object_hash_is_stable_and_content_addressed(self):
        obj = {"type": "profile", "author": "aa", "version": 3}
        # A known vector: the hash is SHA-256 of the canonical bytes.
        import hashlib

        expected = hashlib.sha256(hub_store.canonical_bytes(obj)).hexdigest()
        assert hub_store.object_hash(obj) == expected
        # Any content change changes the address.
        changed = {**obj, "version": 4}
        assert hub_store.object_hash(changed) != expected


class TestSignVerify:
    def test_sign_then_verify_round_trips(self, data_dir):
        pub = identity.public_identity()["signing_pubkey"]
        obj = {"type": "profile", "author": identity.signing_fingerprint(), "version": 1}
        signed = hub_store.sign_object(obj)
        assert "sig" in signed
        assert hub_store.verify_object(signed, pub) is True

    def test_tampered_object_fails_verification(self, data_dir):
        pub = identity.public_identity()["signing_pubkey"]
        signed = hub_store.sign_object(
            {"type": "profile", "author": identity.signing_fingerprint(), "version": 1}
        )
        tampered = {**signed, "version": 2}
        assert hub_store.verify_object(tampered, pub) is False

    def test_wrong_key_fails_verification(self, data_dir, tmp_path, monkeypatch):
        signed = hub_store.sign_object(
            {"type": "profile", "author": identity.signing_fingerprint(), "version": 1}
        )
        # Mint a different identity in an isolated dir; its key must not verify.
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path / "other"))
        other_pub = identity.public_identity()["signing_pubkey"]
        assert hub_store.verify_object(signed, other_pub) is False

    def test_verify_never_raises_on_missing_or_garbage_sig(self, data_dir):
        pub = identity.public_identity()["signing_pubkey"]
        assert hub_store.verify_object({"type": "x"}, pub) is False
        assert hub_store.verify_object({"type": "x", "sig": 123}, pub) is False


class TestStoreRoundTrip:
    @pytest.mark.asyncio
    async def test_object_round_trip(self, store):
        signed = hub_store.sign_object(
            {"type": "profile", "author": identity.signing_fingerprint(), "version": 1}
        )
        h = await store.put_object(signed)
        assert h == hub_store.object_hash(signed)
        got = await store.get_object(h)
        assert got == signed

    @pytest.mark.asyncio
    async def test_put_object_is_idempotent(self, store):
        signed = hub_store.sign_object(
            {"type": "profile", "author": identity.signing_fingerprint(), "version": 1}
        )
        h1 = await store.put_object(signed)
        h2 = await store.put_object(signed)
        assert h1 == h2

    @pytest.mark.asyncio
    async def test_put_object_requires_signature(self, store):
        with pytest.raises(ValueError):
            await store.put_object({"type": "profile", "author": "aa"})

    @pytest.mark.asyncio
    async def test_blob_round_trip(self, store):
        data = b"\x00\x01\x02 not text \xff"
        h = await store.put_blob(data, mime="image/webp")
        blob = await store.get_blob(h)
        assert blob["data"] == data
        assert blob["size"] == len(data)
        assert blob["mime"] == "image/webp"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store):
        assert await store.get_object("nope") is None
        assert await store.get_blob("nope") is None

    @pytest.mark.asyncio
    async def test_author_upsert_round_trip(self, store):
        await store.upsert_author(
            "fp1", username="alice", signing_pubkey="aa", encryption_pubkey="bb"
        )
        got = await store.get_author("fp1")
        assert got["username"] == "alice"
        # Upsert overwrites the mapping (a rename or key rotation).
        await store.upsert_author("fp1", username="alice2", signing_pubkey="cc")
        got = await store.get_author("fp1")
        assert got["username"] == "alice2"
        assert got["signing_pubkey"] == "cc"


class TestProfileVersionWins:
    def _profile(self, version: int, display_name: str = "Alice") -> dict:
        return hub_store.sign_object(
            hub_store.build_profile(
                version=version,
                kind="personal",
                display_name=display_name,
                author=identity.signing_fingerprint(),
            )
        )

    @pytest.mark.asyncio
    async def test_highest_version_wins(self, store, data_dir):
        author = identity.signing_fingerprint()
        await store.put_profile(self._profile(7, "v7"))
        assert (await store.get_profile(author))["version"] == 7
        # A stale (lower) version must not clobber the current profile.
        stale = self._profile(5, "v5")
        returned = await store.put_profile(stale)
        assert returned["version"] == 7
        assert (await store.get_profile(author))["display_name"] == "v7"
        # A newer version supersedes.
        await store.put_profile(self._profile(8, "v8"))
        current = await store.get_profile(author)
        assert current["version"] == 8
        assert current["display_name"] == "v8"

    @pytest.mark.asyncio
    async def test_equal_version_is_ignored(self, store, data_dir):
        author = identity.signing_fingerprint()
        await store.put_profile(self._profile(2, "first"))
        await store.put_profile(self._profile(2, "second"))
        assert (await store.get_profile(author))["display_name"] == "first"

    @pytest.mark.asyncio
    async def test_build_profile_rejects_bad_kind(self, data_dir):
        with pytest.raises(ValueError):
            hub_store.build_profile(version=1, kind="robot", display_name="x")
