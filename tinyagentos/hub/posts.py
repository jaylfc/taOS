"""Post objects + chain logic + image ingest -- hub social slice 4.

See ``docs/design/hub-social-network-foundation.md`` ("Post", "Data model", and
the slice plan, slice 4). This is the data-model slice that makes posts real:
the per-author hash *chain* (``seq`` + ``prev``), append, verification, signed
tombstones, and image ingest (re-encode, strip EXIF, blob store). There is still
no peer sync (slice 5); this slice only makes the node's own posts correct and
self-verifying, and surfaces them in the own-timeline.

Chain shape (design "Post")::

    {"type": "post", "author": <fingerprint>, "seq": N, "prev": <hash|null>,
     "created_at": <iso>, "visibility": "circle"|"public",
     "body": {"text": ..., "format": "md-subset"},
     "attachments": [{"blob": <sha256>, "size": int, "mime": str}],
     "sig": <ed25519 over canonical bytes>}

``seq`` + ``prev`` make each author's posts a hash chain: total order within an
author (no clock trust needed), tamper evidence (a cacher or a tamperer cannot
alter or reorder a post without breaking the chain), and cheap gap detection.
The chain index (``hub_chain`` in the store) records every entry's hash and its
``prev`` even after a post body is dropped by a tombstone, so the chain stays
verifiable after deletion (design "Deletion: signed tombstones, honestly").

Privacy tiers (design "Privacy tiers"): two tiers, enforced by encryption in
slice 7. Until then a ``circle`` (friends-only) post is, per the design's
explicitly-temporary note, labeled as such and enforced later by
serve-authorization; the composer makes the choice loud. The body for both tiers
is plaintext here and slice 7 wraps it.
"""
from __future__ import annotations

import base64
import hashlib
import io
from datetime import datetime, timezone
from typing import Optional

from tinyagentos.hub import identity, store as hub_store

# Post visibility tiers (design "Privacy tiers"). Slice 7 adds real friends-only
# encryption; until then a "circle" post is enforced by serve-authorization only
# and the composer labels it accordingly (design, slice 7 note).
POST_VISIBILITY = ("public", "circle")

# Cap image dimensions on ingest so cached blobs stay small (design open-question
# 4: images capped per post). Larger inputs are downscaled before re-encoding.
MAX_IMAGE_DIMENSION = 2048

# Re-encode ingested images to this format. WEBP keeps quality at a small size and
# (unlike JPEG) does not carry the source EXIF through a re-save, which is exactly
# the strip we want.
_INGEST_FORMAT = "WEBP"
_INGEST_MIME = "image/webp"
_INGEST_QUALITY = 85


def _now_iso() -> str:
    """UTC ISO-8601 without microseconds, matching the design's ``created_at``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- post object --------------------------------------------------------------


def build_post(
    *,
    visibility: str,
    text: str,
    attachments: Optional[list] = None,
    author: Optional[str] = None,
) -> dict:
    """Build an *unsigned* post object (design "Post").

    ``author`` defaults to this node's signing fingerprint, so the common case
    (publishing your own post) needs no key handling from the caller. ``seq`` and
    ``prev`` are left at 0/None here; :func:`append_post` fills them from the
    chain head before signing. Pass :func:`store.sign_object` over the result to
    sign it (or just call :func:`append_post`, which does).
    """
    if visibility not in POST_VISIBILITY:
        raise ValueError(
            f"invalid visibility {visibility!r}; expected one of {POST_VISIBILITY}"
        )
    return {
        "type": "post",
        "author": author or identity.signing_fingerprint(),
        "seq": 0,
        "prev": None,
        "created_at": _now_iso(),
        "visibility": visibility,
        "body": {"text": text, "format": "md-subset"},
        "attachments": attachments or [],
    }


def build_tombstone(author: str, target_hash: str, seq: int, prev) -> dict:
    """Build an *unsigned* tombstone for ``target_hash`` (design "Deletion").

    A tombstone is a chain entry in its own right (it carries ``seq``/``prev`` and
    is signed), so deleting a post extends the chain rather than breaking it.
    """
    return {
        "type": "tombstone",
        "author": author,
        "seq": seq,
        "prev": prev,
        "created_at": _now_iso(),
        "target": target_hash,
    }


# --- chain logic --------------------------------------------------------------


async def next_chain_position(store: hub_store.HubStore, author: str) -> tuple[int, Optional[str]]:
    """Return ``(next_seq, prev_hash)`` for appending to ``author``'s chain.

    The first entry is ``seq=1`` with ``prev=None``; every later entry continues
    from the current head's hash, which is what makes the chain tamper-evident.
    """
    head = await store.get_chain_head(author)
    if head is None:
        return 1, None
    return head["seq"] + 1, head["hash"]


async def append_post(
    store: hub_store.HubStore,
    *,
    visibility: str,
    text: str,
    attachments: Optional[list] = None,
    author: Optional[str] = None,
) -> dict:
    """Build, chain-position, sign, and store a post; return the signed post.

    Fills ``seq``/``prev`` from the live chain head so the new post continues the
    hash chain, signs it with this node's key, and records it in both the object
    store and the permanent chain index.
    """
    author = author or identity.signing_fingerprint()
    seq, prev = await next_chain_position(store, author)
    post = build_post(
        visibility=visibility, text=text, attachments=attachments, author=author
    )
    post["seq"] = seq
    post["prev"] = prev
    signed = hub_store.sign_object(post)
    await store.put_chain_object(signed)
    return signed


async def delete_post(
    store: hub_store.HubStore,
    post_hash: str,
    author: Optional[str] = None,
) -> dict:
    """Tombstone a post: append a signed tombstone and drop the post's content.

    Compliant nodes drop the post body and its blobs on receipt (design
    "Deletion"). The tombstone itself remains in the chain index so the chain
    stays verifiable. Raises ``ValueError`` if the post is unknown.
    """
    author = author or identity.signing_fingerprint()
    post = await store.get_object(post_hash)
    if post is None:
        raise ValueError("post not found")
    seq, prev = await next_chain_position(store, author)
    tomb = build_tombstone(author, post_hash, seq, prev)
    signed_tomb = hub_store.sign_object(tomb)
    await store.put_chain_object(signed_tomb)
    # Drop the post body and every blob it referenced. The tombstone's index row
    # survives, so the chain head advances and stays verifiable.
    for att in post.get("attachments") or []:
        blob = att.get("blob") if isinstance(att, dict) else None
        if blob:
            await store.drop_blob(blob)
    await store.drop_object(post_hash)
    return signed_tomb


async def verify_chain(store: hub_store.HubStore, author: str) -> tuple[bool, Optional[str]]:
    """Walk ``author``'s chain index and verify linkage + signatures.

    Returns ``(ok, error)``. ``ok`` is False (with a human-readable ``error``)
    when any ``prev`` does not link to the previous entry's hash (chain broken /
    reordered) or when any entry that still has its body fails signature
    verification (tampered). Entries whose body was dropped by a tombstone are
    linkage-checked only, since their signature can no longer be recomputed,
    which is exactly why the chain index must persist past deletion.
    """
    entries = await store.list_chain_entries(author)
    pub = identity.public_identity()["signing_pubkey"]
    prev_hash: Optional[str] = None
    for entry in entries:
        if entry.get("prev_hash") != prev_hash:
            return (
                False,
                f"seq {entry['seq']}: prev {entry.get('prev_hash')!r} "
                f"does not link to previous entry {prev_hash!r} (chain broken)",
            )
        obj = await store.get_object(entry["hash"])
        if obj is not None and not hub_store.verify_object(obj, pub):
            return False, f"seq {entry['seq']}: signature invalid (tampered)"
        prev_hash = entry["hash"]
    return True, None


# --- image ingest -------------------------------------------------------------


def ingest_image(raw: bytes, mime: Optional[str] = None) -> dict:
    """Re-encode and strip EXIF from an image, returning a blob-ready dict.

    The image is decoded, orientation is applied (so it renders upright), it is
    flattened to RGB, downscaled if larger than ``MAX_IMAGE_DIMENSION``, and
    re-encoded to WEBP. Re-encoding to WEBP does not copy the source EXIF, so the
    output carries no metadata (design: "Images are re-encoded on ingest (strip
    EXIF, cap dimensions) before hashing"). Returns
    ``{"blob", "size", "mime", "data"}``; ``data`` is the bytes the caller stores
    in the blob store and ``blob`` is its SHA-256 content address. Raises
    ``ValueError`` on non-image input.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - Pillow is a hard dep for ingest
        raise ValueError("image ingest requires Pillow") from exc

    try:
        img = Image.open(io.BytesIO(raw))
        # Apply EXIF orientation, then drop the EXIF: ImageOps.exif_transpose reads
        # the orientation tag and bakes it into the pixels, so the subsequent
        # re-save needs no EXIF and writes none.
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        if max(img.size) > MAX_IMAGE_DIMENSION:
            img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
        buf = io.BytesIO()
        img.save(buf, format=_INGEST_FORMAT, quality=_INGEST_QUALITY)
        data = buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - any decode failure is "not an image"
        raise ValueError(f"invalid image: {exc}") from exc

    return {
        "blob": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mime": _INGEST_MIME,
        "data": data,
    }


def decode_attachment_data(data: str) -> bytes:
    """Decode an ``attachment.data`` base64 string (with or without a data: URI
    prefix) into raw bytes for :func:`ingest_image`."""
    if "," in data and data.strip().startswith("data:"):
        data = data.split(",", 1)[1]
    return base64.b64decode(data)
