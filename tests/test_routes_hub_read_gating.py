"""The box's hub chain is readable by an admin only (hub social, pre-phase 1).

Design hub-social-p2p-architecture section 7: the local read routes
(``/api/hub/timeline``, ``/api/hub/chain``, ``/api/hub/blobs/{hash}``) expose the
box identity's whole chain, friends-only bodies and every blob. A second
signed-in member of the box must get 403; an admin keeps full access.
"""
from __future__ import annotations

import base64
import io

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image
from taos_test_csrf import csrf_event_hooks


@pytest.fixture(autouse=True)
def _isolate_hub_data(tmp_data_dir, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
    return tmp_data_dir


def _png_data_uri() -> str:
    img = Image.new("RGB", (8, 8), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest_asyncio.fixture
async def member(app, client):
    """A signed-in, non-admin member of the box. ``client`` (the admin) first."""
    invite = app.state.auth.add_user_invite("bob", "admin")
    app.state.auth.complete_invite("bob", invite, "Bob", "bob@test.com", "bobspass11")
    token = app.state.auth.create_session(user_id=app.state.auth.find_user("bob")["id"], long_lived=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
    ) as c:
        yield c


@pytest_asyncio.fixture
async def blob_hash(client) -> str:
    resp = await client.post("/api/hub/posts", json={
        "text": "friends only", "attachments": [{"data": _png_data_uri(), "mime": "image/png"}],
    })
    assert resp.status_code == 200
    return resp.json()["post"]["attachments"][0]["blob"]


@pytest.mark.asyncio
async def test_a_member_cannot_read_the_box_chain(member, blob_hash):
    assert (await member.get("/api/hub/timeline")).status_code == 403
    assert (await member.get("/api/hub/chain")).status_code == 403
    assert (await member.get(f"/api/hub/blobs/{blob_hash}")).status_code == 403


@pytest.mark.asyncio
async def test_the_admin_still_reads_the_box_chain(client, blob_hash):
    timeline = await client.get("/api/hub/timeline")
    assert timeline.status_code == 200
    assert [p["body"]["text"] for p in timeline.json()["posts"]] == ["friends only"]
    assert (await client.get("/api/hub/chain")).json()["head"]["seq"] == 1
    assert (await client.get(f"/api/hub/blobs/{blob_hash}")).status_code == 200
