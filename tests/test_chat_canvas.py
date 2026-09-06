from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.chat.canvas import CanvasStore


# ── Store tests ───────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def canvas_store(tmp_path):
    store = CanvasStore(tmp_path / "canvas_test.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_create_canvas(canvas_store):
    canvas = await canvas_store.create(
        title="Test Canvas",
        content="# Hello",
        created_by="agent1",
    )
    assert canvas["id"] and len(canvas["id"]) == 8
    assert canvas["edit_token"] and len(canvas["edit_token"]) == 16
    assert canvas["title"] == "Test Canvas"
    assert canvas["content"] == "# Hello"
    assert canvas["created_by"] == "agent1"


@pytest.mark.asyncio
async def test_get_canvas(canvas_store):
    canvas = await canvas_store.create(title="Get Test", content="Some content", created_by="agent1")
    fetched = await canvas_store.get(canvas["id"])
    assert fetched is not None
    assert fetched["content"] == "Some content"
    assert fetched["title"] == "Get Test"


@pytest.mark.asyncio
async def test_update_canvas(canvas_store):
    canvas = await canvas_store.create(title="Update Test", content="old content", created_by="agent1")
    result = await canvas_store.update(canvas["id"], canvas["edit_token"], content="new content")
    assert result is True
    fetched = await canvas_store.get(canvas["id"])
    assert fetched["content"] == "new content"


@pytest.mark.asyncio
async def test_update_wrong_token(canvas_store):
    canvas = await canvas_store.create(title="Token Test", content="original", created_by="agent1")
    result = await canvas_store.update(canvas["id"], "wrongtoken123456", content="hacked")
    assert result is False
    fetched = await canvas_store.get(canvas["id"])
    assert fetched["content"] == "original"


@pytest.mark.asyncio
async def test_delete_canvas(canvas_store):
    canvas = await canvas_store.create(title="Delete Me", content="bye", created_by="agent1")
    deleted = await canvas_store.delete(canvas["id"])
    assert deleted is True
    fetched = await canvas_store.get(canvas["id"])
    assert fetched is None


@pytest.mark.asyncio
async def test_list_canvases(canvas_store):
    await canvas_store.create(title="Canvas 1", content="a", created_by="agent1")
    await canvas_store.create(title="Canvas 2", content="b", created_by="agent1")
    await canvas_store.create(title="Canvas 3", content="c", created_by="agent1")
    canvases = await canvas_store.list_all()
    assert len(canvases) == 3


# ── Route tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_generate_canvas_api(client):
    resp = await client.post("/api/canvas/generate", json={
        "title": "API Canvas",
        "content": "# Generated",
        "agent_name": "test-agent",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "canvas_id" in data
    assert data["canvas_url"].startswith("/canvas/")
    assert "edit_token" in data


@pytest.mark.asyncio
async def test_update_canvas_api(client):
    create_resp = await client.post("/api/canvas/generate", json={
        "title": "Update Test",
        "content": "original",
    })
    data = create_resp.json()
    canvas_id = data["canvas_id"]
    edit_token = data["edit_token"]

    update_resp = await client.post(f"/api/canvas/{canvas_id}/update", json={
        "edit_token": edit_token,
        "content": "updated content",
    })
    assert update_resp.status_code == 200
    assert update_resp.json()["status"] == "updated"


@pytest.mark.asyncio
async def test_update_canvas_wrong_token(client):
    create_resp = await client.post("/api/canvas/generate", json={
        "title": "Bad Token Test",
        "content": "safe",
    })
    canvas_id = create_resp.json()["canvas_id"]

    update_resp = await client.post(f"/api/canvas/{canvas_id}/update", json={
        "edit_token": "badtoken1234567",
        "content": "hacked",
    })
    assert update_resp.status_code == 403
    assert "Invalid" in update_resp.json()["error"]


@pytest.mark.asyncio
async def test_canvas_data_api(client):
    create_resp = await client.post("/api/canvas/generate", json={
        "title": "Data Test",
        "content": "some content here",
    })
    canvas_id = create_resp.json()["canvas_id"]

    data_resp = await client.get(f"/api/canvas/{canvas_id}/data")
    assert data_resp.status_code == 200
    data = data_resp.json()
    assert data["id"] == canvas_id
    assert data["content"] == "some content here"
    assert data["title"] == "Data Test"


@pytest.mark.asyncio
async def test_delete_canvas_api(client):
    create_resp = await client.post("/api/canvas/generate", json={
        "title": "Delete Test",
        "content": "to be deleted",
    })
    canvas_id = create_resp.json()["canvas_id"]

    del_resp = await client.delete(f"/api/canvas/{canvas_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    data_resp = await client.get(f"/api/canvas/{canvas_id}/data")
    assert data_resp.status_code == 404


@pytest.mark.asyncio
async def test_list_canvases_api(client):
    await client.post("/api/canvas/generate", json={"title": "Canvas A", "content": "a"})
    await client.post("/api/canvas/generate", json={"title": "Canvas B", "content": "b"})

    list_resp = await client.get("/api/canvas")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert "canvases" in data
    assert len(data["canvases"]) >= 2
