"""Post objects, chain logic, and image ingest (hub social slice 4).

Covers the things slice 4 in ``docs/design/hub-social-network-foundation.md``
calls out:

- **chain append/verify**: posts append to a per-author hash chain (``seq`` +
  ``prev``) and the chain verifies (linkage + signatures) end to end;
- **tamper detection**: altering a stored post's body, or breaking a chain
  ``prev`` link, fails verification;
- **tombstone drops content and keeps the chain verifiable**: deleting a post
  drops its body and blobs but the chain index survives, so the chain still
  verifies and the head advances;
- **EXIF stripped**: image ingest re-encodes and removes EXIF metadata before
  the bytes are hashed into a blob.
"""
from __future__ import annotations

import io

import pytest
import pytest_asyncio
from PIL import Image

from tinyagentos.hub import identity, posts
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


class TestChainAppendVerify:
    @pytest.mark.asyncio
    async def test_posts_append_in_chain_order(self, store, data_dir):
        p1 = await posts.append_post(store, visibility="public", text="first")
        p2 = await posts.append_post(store, visibility="circle", text="second")
        p3 = await posts.append_post(store, visibility="public", text="third")

        # seq + prev link the chain; prev of p1 is None.
        assert p1["seq"] == 1 and p1["prev"] is None
        assert p2["seq"] == 2 and p2["prev"] == hub_store.object_hash(p1)
        assert p3["seq"] == 3 and p3["prev"] == hub_store.object_hash(p2)

        # Each is signed by this node and verifies against its own key.
        pub = identity.public_identity()["signing_pubkey"]
        for p in (p1, p2, p3):
            assert hub_store.verify_object(p, pub) is True

    @pytest.mark.asyncio
    async def test_verify_chain_passes_for_intact_chain(self, store, data_dir):
        await posts.append_post(store, visibility="public", text="a")
        await posts.append_post(store, visibility="circle", text="b")
        await posts.append_post(store, visibility="public", text="c")
        ok, error = await posts.verify_chain(store, identity.signing_fingerprint())
        assert ok is True
        assert error is None

    @pytest.mark.asyncio
    async def test_timeline_lists_own_posts_in_order(self, store, data_dir):
        await posts.append_post(store, visibility="public", text="a")
        await posts.append_post(store, visibility="circle", text="b")
        timeline = await store.list_posts(identity.signing_fingerprint())
        assert [p["body"]["text"] for p in timeline] == ["a", "b"]
        # list_posts augments each post with its content-address hash.
        assert all(p.get("hash") for p in timeline)


class TestTamperDetection:
    @pytest.mark.asyncio
    async def test_tampered_post_body_fails_object_verify(self, store, data_dir):
        pub = identity.public_identity()["signing_pubkey"]
        p = await posts.append_post(store, visibility="public", text="original")
        assert hub_store.verify_object(p, pub) is True
        tampered = {**p, "body": {"text": "edited", "format": "md-subset"}}
        assert hub_store.verify_object(tampered, pub) is False

    @pytest.mark.asyncio
    async def test_tampered_stored_body_fails_chain_verify(self, store, data_dir):
        author = identity.signing_fingerprint()
        p = await posts.append_post(store, visibility="public", text="real")
        await posts.append_post(store, visibility="circle", text="more")

        # Mutate the stored body in place (same content-address key) so the
        # signature no longer matches the bytes -> chain verification must fail.
        tampered_body = hub_store.canonical_json(
            {**p, "body": {"text": "hacked", "format": "md-subset"}}
        )
        await store._db.execute(
            "UPDATE hub_objects SET body = ? WHERE hash = ?",
            (tampered_body, hub_store.object_hash(p)),
        )
        await store._db.commit()

        ok, error = await posts.verify_chain(store, author)
        assert ok is False
        assert error is not None and "tampered" in error

    @pytest.mark.asyncio
    async def test_broken_prev_link_fails_chain_verify(self, store, data_dir):
        author = identity.signing_fingerprint()
        await posts.append_post(store, visibility="public", text="a")
        await posts.append_post(store, visibility="circle", text="b")

        # Break the chain: point the second entry's prev at the wrong hash.
        await store._db.execute(
            "UPDATE hub_chain SET prev_hash = 'deadbeef' WHERE seq = 2 AND author = ?",
            (author,),
        )
        await store._db.commit()

        ok, error = await posts.verify_chain(store, author)
        assert ok is False
        assert error is not None and "chain broken" in error


class TestTombstone:
    @pytest.mark.asyncio
    async def test_delete_drops_content_but_keeps_chain_verifiable(self, store, data_dir):
        author = identity.signing_fingerprint()
        await posts.append_post(store, visibility="public", text="keep me")
        p2 = await posts.append_post(store, visibility="circle", text="delete me")

        tomb = await posts.delete_post(store, hub_store.object_hash(p2))

        # The post body and its blobs are gone from the stores...
        assert await store.get_object(hub_store.object_hash(p2)) is None
        # ...but the tombstone is a real chain entry (next seq, prev links head).
        assert tomb["type"] == "tombstone"
        assert tomb["seq"] == 3
        assert tomb["prev"] == hub_store.object_hash(p2)
        assert tomb["target"] == hub_store.object_hash(p2)

        # The chain still verifies (index survived) and the head advanced to the
        # tombstone; the deleted post no longer appears in the own-timeline.
        ok, error = await posts.verify_chain(store, author)
        assert ok is True
        assert error is None
        head = await store.get_chain_head(author)
        assert head["seq"] == 3 and head["type"] == "tombstone"
        timeline = await store.list_posts(author)
        assert [p["body"]["text"] for p in timeline] == ["keep me"]

    @pytest.mark.asyncio
    async def test_delete_drops_referenced_blobs(self, store, data_dir):
        # Store a blob, build a post that references it, then delete the post.
        blob = b"\x89PNG\r\n\x1a\n fake png bytes"
        h = await store.put_blob(blob, mime="image/png")
        post = await posts.append_post(
            store, visibility="public", text="with image",
            attachments=[{"blob": h, "size": len(blob), "mime": "image/png"}],
        )
        assert (await store.get_blob(h)) is not None
        await posts.delete_post(store, hub_store.object_hash(post))
        assert await store.get_blob(h) is None

    @pytest.mark.asyncio
    async def test_delete_unknown_post_raises(self, store, data_dir):
        with pytest.raises(ValueError):
            await posts.delete_post(store, "nonexistent-hash")


class TestImageIngest:
    def _make_exif_jpeg(self) -> bytes:
        # Build a JPEG carrying EXIF metadata (a Make tag), so we can prove the
        # ingest path strips it.
        img = Image.new("RGB", (16, 16), (120, 60, 200))
        exif = img.getexif()
        exif[0x010F] = "LeakyCamera"  # Make
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif.tobytes())
        return buf.getvalue()

    def test_ingest_strips_exif_and_returns_blob(self, data_dir):
        raw = self._make_exif_jpeg()
        # The source really carries EXIF.
        assert "LeakyCamera".encode() in raw or b"Exif" in raw

        out = posts.ingest_image(raw, "image/jpeg")

        assert out["mime"] == "image/webp"
        assert out["size"] > 0
        assert out["blob"] == __import__("hashlib").sha256(out["data"]).hexdigest()
        # The re-encoded output must not carry the source EXIF string.
        assert b"LeakyCamera" not in out["data"]
        reopened = Image.open(io.BytesIO(out["data"]))
        assert "exif" not in reopened.info

    def test_ingest_caps_large_dimensions(self, data_dir):
        big = Image.new("RGB", (4000, 3000), (10, 20, 30))
        buf = io.BytesIO()
        big.save(buf, "PNG")
        out = posts.ingest_image(buf.getvalue(), "image/png")
        reopened = Image.open(io.BytesIO(out["data"]))
        assert max(reopened.size) <= posts.MAX_IMAGE_DIMENSION

    def test_ingest_rejects_non_image(self, data_dir):
        with pytest.raises(ValueError):
            posts.ingest_image(b"this is not an image", "text/plain")
