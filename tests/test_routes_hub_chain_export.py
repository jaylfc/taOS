"""Local chain export and blob read (hub social phase 0, local only).

``GET /api/hub/chain`` is the payload a peer will later pull, served to the
box's own users only: a page of chain entries plus every newer tombstone, even
when the page is truncated. ``GET /api/hub/blobs/{hash}`` returns an ingested
image so the Hub app can render it, sandboxed and never sniffed.
"""
from __future__ import annotations

import base64
import io

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def _isolate_hub_data(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
    return tmp_data_dir


def _png_data_uri() -> str:
    img = Image.new("RGB", (8, 8), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


async def _post(client, text: str, **extra) -> dict:
    resp = await client.post("/api/hub/posts", json={"text": text, **extra})
    assert resp.status_code == 200
    return resp.json()["post"]


class TestChainExport:
    @pytest.mark.asyncio
    async def test_no_identity_before_first_post(self, client):
        resp = await client.get("/api/hub/chain")
        assert resp.status_code == 200
        assert resp.json() == {"state": "no-identity"}

    @pytest.mark.asyncio
    async def test_returns_signed_entries_in_order_with_head(self, client):
        p1 = await _post(client, "one")
        p2 = await _post(client, "two", visibility="public")

        body = (await client.get("/api/hub/chain")).json()
        assert body["state"] == "ok"
        assert body["head"] == {"seq": 2, "hash": p2["hash"]}
        assert [e["seq"] for e in body["entries"]] == [1, 2]
        assert body["entries"][0]["hash"] == p1["hash"]
        assert body["entries"][1]["prev_hash"] == p1["hash"]
        assert body["entries"][0]["object"]["sig"]
        assert body["entries"][0]["object"]["body"]["text"] == "one"
        assert body["tombstones"] == []

    @pytest.mark.asyncio
    async def test_after_seq_and_limit_page_the_chain(self, client):
        for i in range(4):
            await _post(client, f"p{i}")
        body = (await client.get("/api/hub/chain", params={"after_seq": 1, "limit": 2})).json()
        assert [e["seq"] for e in body["entries"]] == [2, 3]
        assert body["head"]["seq"] == 4

    @pytest.mark.asyncio
    async def test_tombstones_travel_even_when_the_page_is_truncated(self, client):
        gone = await _post(client, "gone")
        await _post(client, "kept")
        await client.post(f"/api/hub/posts/{gone['hash']}/delete")

        body = (await client.get("/api/hub/chain", params={"limit": 1})).json()
        # The page stops at seq 1, whose body the tombstone dropped...
        assert [e["seq"] for e in body["entries"]] == [1]
        assert body["entries"][0]["object"] is None
        # ...but the seq-3 tombstone is still delivered.
        assert [t["target"] for t in body["tombstones"]] == [gone["hash"]]
        assert body["tombstones"][0]["sig"]

    @pytest.mark.asyncio
    async def test_rejects_bad_paging(self, client):
        assert (await client.get("/api/hub/chain", params={"limit": 0})).status_code == 400
        assert (await client.get("/api/hub/chain", params={"limit": 501})).status_code == 400
        assert (await client.get("/api/hub/chain", params={"after_seq": -1})).status_code == 400

    @pytest.mark.asyncio
    async def test_after_seq_beyond_sqlite_integer_range_is_a_400(self, client):
        await _post(client, "p")
        resp = await client.get("/api/hub/chain", params={"after_seq": 2**63})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_tombstones_respect_after_seq(self, client):
        gone = await _post(client, "gone")
        await _post(client, "kept")
        await client.post(f"/api/hub/posts/{gone['hash']}/delete")

        body = (await client.get("/api/hub/chain", params={"after_seq": 3})).json()
        assert body["tombstones"] == []


class TestBlobRead:
    @pytest.mark.asyncio
    async def test_returns_the_ingested_image_sandboxed(self, client):
        post = await _post(client, "pic", attachments=[{"data": _png_data_uri(), "mime": "image/png"}])
        att = post["attachments"][0]

        resp = await client.get(f"/api/hub/blobs/{att['blob']}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in resp.headers["content-security-policy"]
        assert len(resp.content) == att["size"]
        Image.open(io.BytesIO(resp.content)).verify()

    @pytest.mark.asyncio
    async def test_unknown_and_malformed_hashes(self, client):
        assert (await client.get(f"/api/hub/blobs/{'0' * 64}")).status_code == 404
        assert (await client.get("/api/hub/blobs/not-a-hash")).status_code == 400
        assert (await client.get(f"/api/hub/blobs/{'A' * 64}")).status_code == 400

    @pytest.mark.asyncio
    async def test_non_image_blob_is_an_opaque_download(self, client, tmp_data_dir):
        from tinyagentos.hub import store as hub_store

        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        try:
            h = await store.put_blob(b"<script>alert(1)</script>", mime="text/html")
        finally:
            await store.close()

        resp = await client.get(f"/api/hub/blobs/{h}")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["content-disposition"] == "attachment"

    @pytest.mark.asyncio
    async def test_requires_a_signed_in_user(self, app):
        from httpx import ASGITransport, AsyncClient

        app.state._startup_complete = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
            blob_resp = await anon.get(f"/api/hub/blobs/{'0' * 64}")
            chain_resp = await anon.get("/api/hub/chain")
        assert blob_resp.status_code == 401
        assert chain_resp.status_code == 401
