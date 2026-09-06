import pytest

from tinyagentos.routes.designs import MAX_CONTENT_BYTES


@pytest.mark.asyncio
async def test_create_list_get_update_delete_design(client):
    resp = await client.post(
        "/api/designs",
        json={"name": "Poster", "content": '{"artboard": {}, "elements": []}'},
    )
    assert resp.status_code == 200
    created = resp.json()
    design_id = created["id"]
    assert created["name"] == "Poster"
    assert created["content"] == '{"artboard": {}, "elements": []}'
    assert isinstance(created["updated_at"], int)

    resp = await client.get("/api/designs")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == design_id
    assert "content" not in items[0]

    resp = await client.get(f"/api/designs/{design_id}")
    assert resp.status_code == 200
    assert resp.json()["content"] == '{"artboard": {}, "elements": []}'

    resp = await client.put(
        f"/api/designs/{design_id}",
        json={"name": "Poster v2", "content": '{"artboard": {}, "elements": [1]}'},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "Poster v2"
    assert updated["content"] == '{"artboard": {}, "elements": [1]}'
    assert updated["updated_at"] >= created["updated_at"]

    resp = await client.delete(f"/api/designs/{design_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get(f"/api/designs/{design_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_name_only_preserves_content(client):
    resp = await client.post(
        "/api/designs",
        json={"name": "Original", "content": '{"elements": [1, 2, 3]}'},
    )
    design_id = resp.json()["id"]

    resp = await client.put(f"/api/designs/{design_id}", json={"name": "Renamed"})
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "Renamed"
    assert updated["content"] == '{"elements": [1, 2, 3]}'


@pytest.mark.asyncio
async def test_create_rejects_missing_name(client):
    resp = await client.post(
        "/api/designs",
        json={"name": "   ", "content": "{}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_missing_returns_404(client):
    resp = await client.get("/api/designs/design-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_returns_404(client):
    resp = await client.put(
        "/api/designs/design-missing",
        json={"name": "X", "content": "{}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_returns_404(client):
    resp = await client.delete("/api/designs/design-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_malformed_json(client):
    resp = await client.post(
        "/api/designs",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_malformed_json(client):
    resp = await client.put(
        "/api/designs/design-missing",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_oversized_content(client):
    resp = await client.post(
        "/api/designs",
        json={"name": "Big", "content": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_update_rejects_oversized_content(client):
    resp = await client.post(
        "/api/designs",
        json={"name": "Poster", "content": "{}"},
    )
    design_id = resp.json()["id"]

    resp = await client.put(
        f"/api/designs/{design_id}",
        json={"content": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413
