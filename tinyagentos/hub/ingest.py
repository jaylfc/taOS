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
  stored post still references.

Cache-set gating, quotas and eviction are phase 2 and are not enforced here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tinyagentos.hub import identity, posts
from tinyagentos.hub import store as hub_store

CHAIN_TYPES = ("post", "tombstone")


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
    prev = obj.get("prev")
    if seq == 1 and prev is not None:
        return "seq 1 must have no prev"
    if seq > 1 and not isinstance(prev, str):
        return "prev missing"
    if obj["type"] == "tombstone" and not isinstance(obj.get("target"), str):
        return "tombstone has no target"
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
        for att in held.get("attachments") or []:
            blob = att.get("blob") if isinstance(att, dict) else None
            if blob and not await store.blob_referenced(blob, exclude=target):
                await store.drop_blob(blob)
        await store.drop_object(target)
    return True


async def chain_gaps(store: hub_store.HubStore, author: str) -> list[int]:
    """Sequence numbers missing below the highest held entry for ``author``."""
    held = {e["seq"] for e in await store.list_chain_entries(author)}
    if not held:
        return []
    return [s for s in range(1, max(held) + 1) if s not in held]


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

    entries = [e for e in (payload or {}).get("entries") or [] if isinstance(e, dict)]
    candidates: dict[str, dict] = {}
    for t in (payload or {}).get("tombstones") or []:
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
        if obj["type"] == "tombstone" and await _apply_tombstone(store, author, obj["target"]):
            report.tombstones_applied.append(obj["target"])

    # A body-less entry (dropped at the origin) is kept as an index row only
    # when a held, verified tombstone of this author targets its hash.
    targets = await _tombstoned(store, author)
    for e in entries:
        seq, h = e.get("seq"), e.get("hash")
        if e.get("object") is not None or h not in targets or not isinstance(seq, int):
            continue
        if await store.get_chain_entry(author, seq) is not None:
            continue
        prev = e.get("prev_hash")
        if (seq == 1) != (prev is None) or await _links(store, author, seq, h, prev):
            continue
        await store.put_chain_index(author, seq, h, prev, "post")
        report.index_only.append(seq)

    report.gaps = await chain_gaps(store, author)
    return report
