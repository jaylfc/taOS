"""Verify-and-store of a foreign chain range (hub social phase 0(c)).

Two in-process stores: ``origin`` holds a foreign author's chain, signed with a
raw Ed25519 key that is not this node's identity, and ``receiver`` ingests the
exact payload ``GET /api/hub/chain`` would serve for it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tinyagentos.hub import identity, ingest, posts
from tinyagentos.hub import store as hub_store


class Author:
    """A foreign author with its own signing key and chain."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.pub = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        self.fp = identity.fingerprint(self.pub)
        self.seq = 0
        self.head = None

    def sign(self, obj: dict) -> dict:
        unsigned = {k: v for k, v in obj.items() if k != "sig"}
        unsigned["sig"] = self.key.sign(hub_store.canonical_bytes(unsigned)).hex()
        return unsigned

    def _next(self, obj: dict) -> dict:
        self.seq += 1
        obj.update(seq=self.seq, prev=self.head)
        signed = self.sign(obj)
        self.head = hub_store.object_hash(signed)
        return signed

    def post(self, text: str, attachments=None) -> dict:
        return self._next(posts.build_post(
            visibility="public", text=text, attachments=attachments, author=self.fp,
        ))

    def tombstone(self, target: str) -> dict:
        return self._next(posts.build_tombstone(self.fp, target, 0, None))


@pytest_asyncio.fixture
async def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    origin = hub_store.HubStore(tmp_path / "origin.db")
    receiver = hub_store.HubStore(tmp_path / "receiver.db")
    await origin.init()
    await receiver.init()
    try:
        yield origin, receiver
    finally:
        await origin.close()
        await receiver.close()


async def _publish(origin, *objs):
    for obj in objs:
        await origin.put_chain_object(obj)
        if obj["type"] == "tombstone":
            await origin.drop_object(obj["target"])


async def _payload(origin, author, after_seq=0, limit=100) -> dict:
    """Exactly what GET /api/hub/chain serves for ``author``."""
    return {
        "entries": await origin.list_chain_range(author, after_seq=after_seq, limit=limit),
        "tombstones": await origin.list_tombstones(author, after_seq=after_seq),
        "head": await origin.get_chain_head(author),
    }


async def _pin(receiver, author: Author):
    await receiver.upsert_author(author.fp, signing_pubkey=author.pub)


async def _object_count(store) -> int:
    cur = await store._db.execute("SELECT COUNT(*) FROM hub_objects")
    return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_happy_path_stores_a_verifiable_chain(stores):
    origin, receiver = stores
    alice = Author()
    await _publish(origin, alice.post("one"), alice.post("two"))
    await _pin(receiver, alice)

    report = await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))

    assert report.stored == [1, 2] and not report.rejected and report.gaps == []
    assert await posts.verify_chain(receiver, alice.fp) == (True, None)
    again = await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))
    assert again.stored == [] and again.rejected == [] and again.forks == []


@pytest.mark.asyncio
async def test_unpinned_author_and_own_chain_are_refused(stores):
    origin, receiver = stores
    alice = Author()
    await _publish(origin, alice.post("one"))
    payload = await _payload(origin, alice.fp)

    report = await ingest.ingest_chain_range(receiver, alice.fp, payload)
    assert report.rejected == [(None, "author has no pinned signing key")]

    identity.load_or_create()
    own = await ingest.ingest_chain_range(receiver, identity.signing_fingerprint(), payload)
    assert own.rejected == [(None, "this node is the origin of its own chain")]
    assert await _object_count(receiver) == 0


@pytest.mark.asyncio
async def test_tampered_mismatched_and_foreign_objects_store_nothing(stores):
    origin, receiver = stores
    alice, mallory = Author(), Author()
    await _publish(origin, alice.post("one"))
    await _pin(receiver, alice)
    payload = await _payload(origin, alice.fp)

    tampered = dict(payload["entries"][0]["object"], body={"text": "edited", "format": "md-subset"})
    forged = mallory.post("not alice")
    bad = {"entries": [
        {"seq": 1, "hash": hub_store.object_hash(tampered), "object": tampered},
        {"seq": 1, "hash": "0" * 64, "object": payload["entries"][0]["object"]},
        {"seq": 1, "hash": hub_store.object_hash(forged), "object": forged},
    ], "tombstones": []}

    report = await ingest.ingest_chain_range(receiver, alice.fp, bad)

    assert report.stored == [] and len(report.rejected) == 3
    assert await _object_count(receiver) == 0
    assert await receiver.list_chain_entries(alice.fp) == []


@pytest.mark.asyncio
async def test_a_fork_is_reported_and_leaves_no_orphaned_body(stores):
    origin, receiver = stores
    alice = Author()
    await _publish(origin, alice.post("one"))
    await _pin(receiver, alice)
    await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))

    alice.seq, alice.head = 0, None
    other = alice.post("a different seq 1")
    fork = {"entries": [{"seq": 1, "hash": hub_store.object_hash(other), "object": other}]}
    report = await ingest.ingest_chain_range(receiver, alice.fp, fork)

    assert report.forks == [1] and report.stored == []
    assert await receiver.get_object(hub_store.object_hash(other)) is None


@pytest.mark.asyncio
async def test_tombstone_in_the_same_range_keeps_the_body_out(stores):
    origin, receiver = stores
    alice = Author()
    gone = alice.post("gone")
    await _publish(origin, gone, alice.post("kept"), alice.tombstone(hub_store.object_hash(gone)))
    await _pin(receiver, alice)
    # Serve the deleted post's body too, as a stale cacher might.
    payload = await _payload(origin, alice.fp)
    payload["entries"][0]["object"] = gone

    report = await ingest.ingest_chain_range(receiver, alice.fp, payload)

    assert report.index_only == [1] and sorted(report.stored) == [2, 3]
    assert await receiver.get_object(hub_store.object_hash(gone)) is None
    assert await posts.verify_chain(receiver, alice.fp) == (True, None)


@pytest.mark.asyncio
async def test_truncated_range_reports_a_gap_and_a_late_body_stays_out(stores):
    origin, receiver = stores
    alice = Author()
    gone = alice.post("gone")
    await _publish(origin, gone, alice.post("kept"), alice.tombstone(hub_store.object_hash(gone)))
    await _pin(receiver, alice)

    # Deletes travel ahead of content: a page cut after seq 2 still carries seq 3.
    page = await _payload(origin, alice.fp, after_seq=1, limit=1)
    report = await ingest.ingest_chain_range(receiver, alice.fp, page)
    assert sorted(report.stored) == [2, 3] and report.gaps == [1]

    late = {"entries": [{"seq": 1, "hash": hub_store.object_hash(gone), "object": gone}]}
    report = await ingest.ingest_chain_range(receiver, alice.fp, late)
    assert report.index_only == [1] and report.gaps == []
    assert await receiver.get_object(hub_store.object_hash(gone)) is None


@pytest.mark.asyncio
async def test_a_later_tombstone_drops_the_cached_body(stores):
    origin, receiver = stores
    alice = Author()
    gone = alice.post("gone")
    await _publish(origin, gone)
    await _pin(receiver, alice)
    await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))
    assert await receiver.get_object(hub_store.object_hash(gone)) is not None

    await _publish(origin, alice.tombstone(hub_store.object_hash(gone)))
    report = await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp, after_seq=1))

    assert report.tombstones_applied == [hub_store.object_hash(gone)]
    assert await receiver.get_object(hub_store.object_hash(gone)) is None
    assert await posts.verify_chain(receiver, alice.fp) == (True, None)


@pytest.mark.asyncio
async def test_a_tombstone_never_drops_another_authors_post(stores):
    origin, receiver = stores
    alice, bob = Author(), Author()
    bobs = bob.post("bob's post")
    await receiver.put_chain_object(bobs)
    await _publish(origin, alice.tombstone(hub_store.object_hash(bobs)))
    await _pin(receiver, alice)

    report = await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))

    assert report.stored == [1] and report.tombstones_applied == []
    assert await receiver.get_object(hub_store.object_hash(bobs)) is not None


@pytest.mark.asyncio
async def test_a_shared_blob_survives_while_another_post_uses_it(stores):
    origin, receiver = stores
    alice = Author()
    data = b"\x89PNG\r\n\x1a\n shared bytes"
    blob = await receiver.put_blob(data, mime="image/png")
    att = [{"blob": blob, "size": len(data), "mime": "image/png"}]
    identity.load_or_create()
    await posts.append_post(receiver, visibility="public", text="mine", attachments=att)
    gone = alice.post("theirs", attachments=att)
    await _publish(origin, gone)
    await _pin(receiver, alice)
    await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))

    await _publish(origin, alice.tombstone(hub_store.object_hash(gone)))
    await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp, after_seq=1))

    assert await receiver.get_object(hub_store.object_hash(gone)) is None
    assert await receiver.has_blob(blob)


def _signed_at(author: Author, seq: int, prev, **fields) -> dict:
    """A post by ``author`` signed at an arbitrary chain position."""
    obj = posts.build_post(visibility="public", text="x", author=author.fp)
    obj.update(fields, seq=seq, prev=prev)
    return author.sign(obj)


def _entry(obj: dict) -> dict:
    return {"seq": obj["seq"], "hash": hub_store.object_hash(obj), "object": obj}


@pytest.mark.asyncio
async def test_a_seq_beyond_sqlite_range_is_rejected_not_raised(stores):
    _origin, receiver = stores
    alice = Author()
    await _pin(receiver, alice)
    huge = _signed_at(alice, 2**63, "a" * 64)

    report = await ingest.ingest_chain_range(receiver, alice.fp, {"entries": [_entry(huge)]})

    assert report.rejected == [(2**63, "seq is out of range")]
    assert await _object_count(receiver) == 0


@pytest.mark.asyncio
async def test_a_far_seq_does_not_make_gap_reporting_unbounded(stores):
    _origin, receiver = stores
    alice = Author()
    await _pin(receiver, alice)
    far = _signed_at(alice, 5000, "a" * 64)

    report = await ingest.ingest_chain_range(receiver, alice.fp, {"entries": [_entry(far)]})

    assert report.stored == [5000]
    assert report.gaps[0] == 1 and len(report.gaps) == 1000


@pytest.mark.asyncio
async def test_non_list_attachments_are_rejected(stores):
    _origin, receiver = stores
    alice = Author()
    await _pin(receiver, alice)
    bad = alice.post("bad", attachments=5)

    report = await ingest.ingest_chain_range(receiver, alice.fp, {"entries": [_entry(bad)]})

    assert report.rejected == [(1, "attachments must be a list")]
    assert await _object_count(receiver) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [["not", "an", "object"], {"entries": 5}, {"tombstones": "x"}])
async def test_a_malformed_payload_is_rejected_not_raised(stores, payload):
    _origin, receiver = stores
    alice = Author()
    await _pin(receiver, alice)

    report = await ingest.ingest_chain_range(receiver, alice.fp, payload)

    assert report.rejected and report.rejected[0][0] is None
    assert report.stored == []


@pytest.mark.asyncio
async def test_an_interrupted_tombstone_is_applied_on_the_next_run(stores):
    _origin, receiver = stores
    alice = Author()
    gone = alice.post("gone")
    # A run that stored the tombstone but stopped before dropping the body.
    await receiver.put_chain_object(gone)
    await receiver.put_chain_object(alice.tombstone(hub_store.object_hash(gone)))
    await _pin(receiver, alice)

    report = await ingest.ingest_chain_range(receiver, alice.fp, {"entries": []})

    assert report.tombstones_applied == [hub_store.object_hash(gone)]
    assert await receiver.get_object(hub_store.object_hash(gone)) is None


@pytest.mark.asyncio
async def test_a_body_less_entry_is_indexed_only_between_held_neighbours(stores):
    origin, receiver = stores
    alice = Author()
    gone = alice.post("gone")
    gone_hash = hub_store.object_hash(gone)
    await _publish(origin, gone, alice.post("kept"), alice.tombstone(gone_hash))
    await _pin(receiver, alice)

    # The real range: seq 1 arrives body-less, anchored by the held seq 2.
    report = await ingest.ingest_chain_range(receiver, alice.fp, await _payload(origin, alice.fp))
    assert report.index_only == [1] and report.gaps == []
    assert await posts.verify_chain(receiver, alice.fp) == (True, None)

    # A peer cannot plant the tombstoned hash at a free seq: the signature
    # covers the hash, not the seq or prev the peer claims for it.
    planted = {"entries": [{"seq": 9, "hash": gone_hash, "prev_hash": "f" * 64, "object": None}]}
    report = await ingest.ingest_chain_range(receiver, alice.fp, planted)
    assert report.index_only == []
    assert report.rejected == [(9, "body-less entry is not anchored by held neighbours")]
    assert await receiver.get_chain_entry(alice.fp, 9) is None


@pytest.mark.asyncio
async def test_a_body_less_entry_over_a_different_held_row_is_a_fork(stores):
    _origin, receiver = stores
    alice = Author()
    held = alice.post("held")
    alice.seq, alice.head = 0, None
    other = alice.post("another seq 1")
    await receiver.put_chain_object(held)
    alice.seq, alice.head = 1, hub_store.object_hash(held)
    await receiver.put_chain_object(alice.tombstone(hub_store.object_hash(other)))
    await _pin(receiver, alice)

    entry = {"seq": 1, "hash": hub_store.object_hash(other), "prev_hash": None, "object": None}
    report = await ingest.ingest_chain_range(receiver, alice.fp, {"entries": [entry]})

    assert report.forks == [1] and report.index_only == []
