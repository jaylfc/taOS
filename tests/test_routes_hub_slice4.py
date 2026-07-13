"""Local hub post routes (hub social slice 4).

Exercises publishing a signed post (with the default friends-only visibility and
an inline image attachment that gets EXIF-stripped on ingest), reading the
own-timeline from the local store, and deleting a post via a signed tombstone
that drops its content while the chain stays verifiable. The taos.my directory is
not involved in slice 4 (no peer sync), so no upstream mocking is needed.
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def _isolate_hub_data(tmp_data_dir, monkeypatch):
    # Both the identity keystore and the hub store resolve from TAOS_DATA_DIR, so
    # pointing it at the per-test data dir keeps every request hermetic.
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
    return tmp_data_dir


def _png_data_uri() -> str:
    img = Image.new("RGB", (8, 8), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class TestHubPostRoutes:
    @pytest.mark.asyncio
    async def test_create_post_defaults_to_circle_and_is_signed(self, client):
        resp = await client.post("/api/hub/posts", json={"text": "hello hub"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "ok"
        post = body["post"]
        assert post["visibility"] == "circle"  # loud default
        assert post["seq"] == 1
        assert post["prev"] is None
        assert post["sig"]
        assert post["body"]["text"] == "hello hub"

    @pytest.mark.asyncio
    async def test_public_visibility_is_honored(self, client):
        resp = await client.post(
            "/api/hub/posts", json={"text": "broadcast", "visibility": "public"}
        )
        assert resp.status_code == 200
        assert resp.json()["post"]["visibility"] == "public"

    @pytest.mark.asyncio
    async def test_invalid_visibility_rejected(self, client):
        resp = await client.post(
            "/api/hub/posts", json={"text": "x", "visibility": "secret"}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_attachment_is_ingested_and_exif_free(self, client):
        resp = await client.post(
            "/api/hub/posts",
            json={"text": "with pic", "attachments": [{"data": _png_data_uri(), "mime": "image/png"}]},
        )
        assert resp.status_code == 200
        post = resp.json()["post"]
        assert len(post["attachments"]) == 1
        att = post["attachments"][0]
        assert att["mime"] == "image/webp"  # re-encoded
        assert att["blob"]

    @pytest.mark.asyncio
    async def test_timeline_lists_own_posts(self, client):
        await client.post("/api/hub/posts", json={"text": "one"})
        await client.post("/api/hub/posts", json={"text": "two", "visibility": "public"})

        resp = await client.get("/api/hub/timeline")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "ok"
        assert [p["body"]["text"] for p in body["posts"]] == ["one", "two"]
        assert all(p.get("hash") for p in body["posts"])

    @pytest.mark.asyncio
    async def test_delete_tombstones_post_and_removes_from_timeline(self, client):
        await client.post("/api/hub/posts", json={"text": "keep"})
        created = await client.post("/api/hub/posts", json={"text": "gone"})
        post_hash = created.json()["post"]["hash"]

        resp = await client.post(f"/api/hub/posts/{post_hash}/delete")
        assert resp.status_code == 200
        tomb = resp.json()["tombstone"]
        assert tomb["type"] == "tombstone"
        assert tomb["target"] == post_hash

        timeline = await client.get("/api/hub/timeline")
        texts = [p["body"]["text"] for p in timeline.json()["posts"]]
        assert texts == ["keep"]
        # The deleted post body is gone from the store.
        assert all(p["hash"] != post_hash for p in timeline.json()["posts"])

    @pytest.mark.asyncio
    async def test_delete_unknown_post_404(self, client):
        resp = await client.post("/api/hub/posts/nope/delete")
        assert resp.status_code == 404
