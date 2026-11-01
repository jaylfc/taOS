"""Verify and store a foreign author's chain range -- hub social phase 0(c).

See ``docs/design/hub-social-p2p-architecture.md`` section 4 ("What a cacher
stores" and "Delete propagation"). A peer pulls a range of another author's
chain in the shape ``GET /api/hub/chain`` returns (``entries``, ``tombstones``,
``head``) and hands it to :func:`ingest_chain_range`. Nothing here talks to the
network; this module only decides what of a received range this node may keep.

Rules, all checked against the author's *pinned* signing key (``hub_authors``),
never against anything the payload claims about itself:

- every object is a ``post`` or ``tombstone`` by that author, its signature
  verifies, and its content address matches the entry it arrived in;
- ``seq``/``prev`` link to the neighbours this node already holds; a missing
  neighbour is a gap (allowed and reported), a different entry at the same
  ``seq`` is a fork (rejected and reported, nothing written);
- tombstones are applied before posts, so a post deleted in the same range is
  never stored with a body; a later copy of a deleted post is recorded as an
  index row only, so deleted content is never re-served;
- a tombstone only drops a post by its own author, and only blobs no other
  stored post still references; every held tombstone is re-applied on each
  run, so a drop interrupted earlier is retried;
- a body-less entry (dropped at the origin) is indexed only when a held
  tombstone targets its hash *and* held neighbours on both sides vouch for its
  ``seq`` and ``prev``: the tombstone's signature covers the hash alone.

Cache-set gating, quotas and eviction are phase 2 and are not enforced here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tinyagentos.hub import identity, posts
from tinyagentos.hub import store as hub_store

CHAIN_TYPES = ("post", "tombstone")
# SQLite stores INTEGER as a signed 64-bit value.
MAX_SEQ = 2**63 - 1
# One signed entry at a far seq must not make gap reporting walk every number.
MAX_REPORTED_GAPS = 1000


@dataclass
class IngestReport:
    """What :func:`ingest_chain_range` did with a received range."""

    stored: list[int] = field(default_factory=list)
    index_only: list[int] = field(default_factory=list)
    tombstones_applied: list[str] = field(default_factory=list)
    rejected: list[tuple[int | None, str]] = field(default_factory=list)
    forks: list[int] = field(default_factory=list)
    gaps: list[int] = field(default_factory=list)


def _check_object(obj: dict, author: str, signing_pubkey: str) -> str | None:
    """Return why ``obj`` is unacceptable, or None when it may be stored."""
    if not isinstance(obj, dict):
        return "not an object"
    if obj.get("type") not in CHAIN_TYPES:
        return f"type {obj.get('type')!r} is not a chain entry"
    if obj.get("author") != author:
        return "author does not match the range author"
    seq = obj.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        return "seq must be a positive integer"
    if seq > MAX_SEQ:
        return "seq is out of range"
    prev = obj.get("prev")
    if seq == 1 and prev is not None:
        return "seq 1 must have no prev"
    if seq > 1 and not isinstance(prev, str):
        return "prev missing"
    if obj["type"] == "tombstone" and not isinstance(obj.get("target"), str):
        return "tombstone has no target"
    attachments = obj.get("attachments")
    if obj["type"] == "post" and attachments is not None and (
        not isinstance(attachments, list)
        or not all(
            isinstance(a, dict) and isinstance(a.get("blob"), (str, type(None)))
            for a in attachments
        )
    ):
        return "attachments must be a list of objects"
    if not hub_store.verify_object(obj, signing_pubkey):
        return "signature does not verify against the pinned key"
    return None


async def _links(store: hub_store.HubStore, author: str, seq: int, h: str, prev) -> str | None:
    """Check ``h`` at ``seq`` against the neighbours already held."""
    before = await store.get_chain_entry(author, seq - 1) if seq > 1 else None
    if before is not None and prev != before["hash"]:
        return "prev does not link to the held previous entry"
    after = await store.get_chain_entry(author, seq + 1)
    if after is not None and after["prev_hash"] != h:
        return "held next entry does not link to this entry"
    return None


async def _tombstoned(store: hub_store.HubStore, author: str) -> set[str]:
    return {t["target"] for t in await store.list_tombstones(author)}


async def _apply_tombstone(store: hub_store.HubStore, author: str, target: str) -> bool:
    """Drop ``target``'s body and its unreferenced blobs; True if it was held."""
    async with posts._append_lock(store):
        held = await store.get_object(target)
        if held is None or held.get("type") != "post" or held.get("author") != author:
            return False
        attachments = held.get("attachments")
        for att in attachments if isinstance(attachments, list) else []:
            blob = att.get("blob") if isinstance(att, dict) else None
            if isinstance(blob, str) and blob and not await store.blob_referenced(blob, exclude=target):
                await store.drop_blob(blob)
        await store.drop_object(target)
    return True


async def chain_gaps(
    store: hub_store.HubStore, author: str, limit: int = MAX_REPORTED_GAPS
) -> list[int]:
    """The first ``limit`` sequence numbers missing below the highest held entry."""
    gaps: list[int] = []
    expected = 1
    for seq in sorted(e["seq"] for e in await store.list_chain_entries(author)):
        while expected < seq and len(gaps) < limit:
            gaps.append(expected)
            expected += 1
        if len(gaps) >= limit:
            break
        expected = seq + 1
    return gaps


async def _anchored(
    store: hub_store.HubStore, author: str, seq: int, h: str, prev
) -> bool:
    """True when held neighbours vouch for a body-less entry's ``seq`` and ``prev``.

    The next entry's signed ``prev`` pins ``h`` to ``seq``; the previous entry's
    hash (or ``seq`` 1 with no ``prev``) pins ``prev``.
    """
    after = await store.get_chain_entry(author, seq + 1)
    if after is None or after["prev_hash"] != h:
        return False
    if seq == 1:
        return prev is None
    before = await store.get_chain_entry(author, seq - 1)
    return before is not None and isinstance(prev, str) and before["hash"] == prev


async def ingest_chain_range(
    store: hub_store.HubStore, author: str, payload: dict
) -> IngestReport:
    """Verify and store what this node may keep of ``author``'s chain range.

    Never raises on bad input: every refused object is listed in ``rejected``
    (or ``forks``) with its seq. Re-ingesting the same payload is a no-op.
    """
    report = IngestReport()
    if identity.exists() and author == identity.signing_fingerprint():
        report.rejected.append((None, "this node is the origin of its own chain"))
        return report
    pinned = await store.get_author(author)
    key = (pinned or {}).get("signing_pubkey")
    if not key:
        report.rejected.append((None, "author has no pinned signing key"))
        return report

    payload = {} if payload is None else payload
    if not isinstance(payload, dict):
        report.rejected.append((None, "payload is not an object"))
        return report
    raw_entries = payload.get("entries") or []
    raw_tombstones = payload.get("tombstones") or []
    if not isinstance(raw_entries, list) or not isinstance(raw_tombstones, list):
        report.rejected.append((None, "entries and tombstones must be lists"))
        return report

    entries = [e for e in raw_entries if isinstance(e, dict)]
    candidates: dict[str, dict] = {}
    for t in raw_tombstones:
        if isinstance(t, dict):
            candidates.setdefault(hub_store.object_hash(t), t)
    for e in entries:
        obj = e.get("object")
        if obj is None:
            continue
        h = hub_store.object_hash(obj) if isinstance(obj, dict) else None
        if h != e.get("hash") or not isinstance(obj, dict) or obj.get("seq") != e.get("seq"):
            report.rejected.append((e.get("seq"), "entry does not match its object"))
            continue
        candidates.setdefault(h, obj)

    valid: list[tuple[str, dict]] = []
    for h, obj in candidates.items():
        reason = _check_object(obj, author, key)
        if reason:
            report.rejected.append((obj.get("seq") if isinstance(obj, dict) else None, reason))
        else:
            valid.append((h, obj))
    # Tombstones first, so a post deleted in this same range is never stored.
    valid.sort(key=lambda item: (item[1]["type"] != "tombstone", item[1]["seq"]))

    for h, obj in valid:
        seq = obj["seq"]
        # Check-then-write under the chain lock, so a concurrent writer cannot
        # take this seq between the check and the insert.
        async with posts._append_lock(store):
            held = await store.get_chain_entry(author, seq)
            if held is not None:
                if held["hash"] != h:
                    report.forks.append(seq)
                continue
            reason = await _links(store, author, seq, h, obj.get("prev"))
            if reason:
                report.rejected.append((seq, reason))
                continue
            if obj["type"] == "post" and h in await _tombstoned(store, author):
                await store.put_chain_index(author, seq, h, obj.get("prev"), "post")
                report.index_only.append(seq)
                continue
            await store.put_chain_object(obj)
            report.stored.append(seq)

    # Every held tombstone, not just this run's: retries a drop that an earlier
    # run stored the tombstone for but did not finish.
    targets = await _tombstoned(store, author)
    for target in sorted(targets):
        if await _apply_tombstone(store, author, target):
            report.tombstones_applied.append(target)

    # A body-less entry (dropped at the origin) is kept as an index row only
    # when a held tombstone targets its hash and held neighbours anchor it.
    for e in entries:
        seq, h = e.get("seq"), e.get("hash")
        if e.get("object") is not None or not isinstance(h, str) or h not in targets:
            continue
        if not isinstance(seq, int) or isinstance(seq, bool) or not 1 <= seq <= MAX_SEQ:
            continue
        prev = e.get("prev_hash")
        async with posts._append_lock(store):
            held = await store.get_chain_entry(author, seq)
            if held is not None:
                if held["hash"] != h:
                    report.forks.append(seq)
                continue
            if not await _anchored(store, author, seq, h, prev):
                report.rejected.append((seq, "body-less entry is not anchored by held neighbours"))
                continue
            await store.put_chain_index(author, seq, h, prev, "post")
            if (await store.get_chain_entry(author, seq) or {}).get("hash") == h:
                report.index_only.append(seq)

    report.gaps = await chain_gaps(store, author)
    return report
