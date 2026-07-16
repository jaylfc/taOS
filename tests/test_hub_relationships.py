"""Follow / friend / circle relationship statements + store (hub social slice 3).

Covers the things slice 3 in ``docs/design/hub-social-network-foundation.md``
calls out: signed follow and cache-grant statements (build, sign, verify the
same way a profile verifies), the default cache quota hint, and the local
relationship store (put / get / list / sever-edges on block). Storage keys are
fingerprints only; usernames never enter a statement.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.hub import identity, relationships
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


class TestRelationshipStatements:
    def test_follow_statement_shape_and_signature(self, data_dir):
        pub = identity.public_identity()["signing_pubkey"]
        fp = identity.signing_fingerprint()
        unsigned = relationships.build_follow_statement("peerFP")
        assert unsigned == {"type": "follow", "author": fp, "target": "peerFP"}
        # Signing is over the same canonical bytes a peer would verify.
        signed = relationships.sign_statement(unsigned)
        assert "sig" in signed
        assert hub_store.verify_object(signed, pub) is True
        # Tampering breaks the signature.
        tampered = {**signed, "target": "otherFP"}
        assert hub_store.verify_object(tampered, pub) is False

    def test_cache_grant_statement_carries_quota_hint(self, data_dir):
        pub = identity.public_identity()["signing_pubkey"]
        fp = identity.signing_fingerprint()
        unsigned = relationships.build_cache_grant_statement("granteeFP", 12345)
        assert unsigned["type"] == "cache-grant"
        assert unsigned["author"] == fp
        assert unsigned["grantee"] == "granteeFP"
        assert unsigned["quota_hint"] == 12345
        signed = relationships.sign_statement(unsigned)
        assert hub_store.verify_object(signed, pub) is True

    def test_cache_grant_defaults_to_a_few_hundred_mb(self, data_dir):
        unsigned = relationships.build_cache_grant_statement("g", relationships.DEFAULT_CACHE_QUOTA_BYTES)
        # A few hundred MB, not bytes: the hint is a real per-friend budget.
        assert relationships.DEFAULT_CACHE_QUOTA_BYTES >= 100 * 1024 * 1024
        assert relationships.DEFAULT_CACHE_QUOTA_BYTES <= 1024 * 1024 * 1024
        assert unsigned["quota_hint"] == relationships.DEFAULT_CACHE_QUOTA_BYTES

    def test_friend_statement_shape(self, data_dir):
        fp = identity.signing_fingerprint()
        unsigned = relationships.build_friend_statement("peerFP")
        assert unsigned == {"type": "friend", "author": fp, "peer": "peerFP"}
        signed = relationships.sign_statement(unsigned)
        assert hub_store.verify_object(signed, identity.public_identity()["signing_pubkey"]) is True

    def test_peer_fingerprint_is_the_author_identifier(self, data_dir):
        # Usernames never appear; the canonical author id is the signing fingerprint.
        follow = relationships.build_follow_statement("peerFP")
        assert "username" not in follow
        assert follow["author"] == identity.signing_fingerprint()


class TestRelationshipStore:
    @pytest.mark.asyncio
    async def test_put_then_get_relationship(self, store, data_dir):
        statement = relationships.sign_statement(
            relationships.build_follow_statement("peerFP")
        )
        await store.put_relationship(
            "peerFP", relationships.REL_FOLLOW_OUT, statement=statement
        )
        got = await store.get_relationship("peerFP", relationships.REL_FOLLOW_OUT)
        assert got["peer"] == "peerFP"
        assert got["kind"] == relationships.REL_FOLLOW_OUT
        assert got["statement"] == statement

    @pytest.mark.asyncio
    async def test_put_relationship_is_idempotent(self, store, data_dir):
        s1 = relationships.sign_statement(relationships.build_follow_statement("p"))
        await store.put_relationship("p", relationships.REL_FOLLOW_OUT, statement=s1)
        s2 = relationships.sign_statement(relationships.build_follow_statement("p"))
        await store.put_relationship("p", relationships.REL_FOLLOW_OUT, statement=s2)
        rows = await store.list_relationships(relationships.REL_FOLLOW_OUT)
        assert len(rows) == 1  # upsert, not duplicate

    @pytest.mark.asyncio
    async def test_cache_grant_stores_quota_hint(self, store, data_dir):
        statement = relationships.sign_statement(
            relationships.build_cache_grant_statement("g", 999)
        )
        await store.put_relationship(
            "g", relationships.REL_CACHE_GRANT, statement=statement, quota_hint=999
        )
        got = await store.get_relationship("g", relationships.REL_CACHE_GRANT)
        assert got["quota_hint"] == 999
        assert got["statement"]["quota_hint"] == 999

    @pytest.mark.asyncio
    async def test_list_relationships_filters_by_kind(self, store, data_dir):
        await store.put_relationship("a", relationships.REL_FOLLOW_OUT)
        await store.put_relationship("b", relationships.REL_FRIEND)
        friends = await store.list_relationships(relationships.REL_FRIEND)
        assert [r["peer"] for r in friends] == ["b"]
        all_rows = await store.list_relationships()
        assert {r["peer"] for r in all_rows} == {"a", "b"}

    @pytest.mark.asyncio
    async def test_sever_edges_removes_every_peer_row(self, store, data_dir):
        await store.put_relationship("p", relationships.REL_FOLLOW_OUT)
        await store.put_relationship("p", relationships.REL_FRIEND)
        await store.put_relationship("p", relationships.REL_CACHE_GRANT, quota_hint=1)
        severed = await store.sever_edges("p")
        assert set(severed) == {
            relationships.REL_FOLLOW_OUT,
            relationships.REL_FRIEND,
            relationships.REL_CACHE_GRANT,
        }
        assert await store.get_relationship("p", relationships.REL_FRIEND) is None

    @pytest.mark.asyncio
    async def test_sever_edges_can_keep_the_block_marker(self, store, data_dir):
        # Block sets the marker, then severs the rest: the block must survive so a
        # block is not wiped by the very purge it triggers.
        await store.put_relationship("p", relationships.REL_FOLLOW_OUT)
        await store.put_relationship("p", relationships.REL_BLOCK)
        await store.sever_edges("p", keep={relationships.REL_BLOCK})
        assert await store.get_relationship("p", relationships.REL_BLOCK) is not None
        assert await store.get_relationship("p", relationships.REL_FOLLOW_OUT) is None

    @pytest.mark.asyncio
    async def test_has_edge_reflects_friend_relationship(self, store, data_dir):
        assert await store.has_edge("p", relationships.REL_FRIEND) is False
        await store.put_relationship("p", relationships.REL_FRIEND)
        assert await store.has_edge("p", relationships.REL_FRIEND) is True

    @pytest.mark.asyncio
    async def test_delete_relationship(self, store, data_dir):
        await store.put_relationship("p", relationships.REL_MUTE)
        await store.delete_relationship("p", relationships.REL_MUTE)
        assert await store.get_relationship("p", relationships.REL_MUTE) is None

    @pytest.mark.asyncio
    async def test_get_author_by_username(self, store, data_dir):
        await store.upsert_author(
            "fpX", username="alice", signing_pubkey="aa", encryption_pubkey="bb"
        )
        got = await store.get_author_by_username("alice")
        assert got["fingerprint"] == "fpX"
        assert await store.get_author_by_username("nobody") is None
