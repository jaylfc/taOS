import pytest

from tinyagentos.routes.web import MAX_CONTENT_BYTES


@pytest.mark.asyncio
async def test_create_list_get_update_delete_site(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": '{"sections": []}'},
    )
    assert resp.status_code == 200
    created = resp.json()
    site_id = created["id"]
    assert created["title"] == "Landing"
    assert created["content"] == '{"sections": []}'
    assert isinstance(created["updated_at"], int)

    resp = await client.get("/api/web/sites")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == site_id
    assert "content" not in items[0]

    resp = await client.get(f"/api/web/sites/{site_id}")
    assert resp.status_code == 200
    assert resp.json()["content"] == '{"sections": []}'

    resp = await client.put(
        f"/api/web/sites/{site_id}",
        json={"title": "Landing v2", "content": '{"sections": [1]}'},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["title"] == "Landing v2"
    assert updated["content"] == '{"sections": [1]}'
    assert updated["updated_at"] >= created["updated_at"]

    resp = await client.delete(f"/api/web/sites/{site_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get(f"/api/web/sites/{site_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_missing_title(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "   ", "content": "{}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_missing_returns_404(client):
    resp = await client.get("/api/web/sites/site-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_returns_404(client):
    resp = await client.put(
        "/api/web/sites/site-missing",
        json={"title": "X", "content": "{}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_missing_returns_404(client):
    resp = await client.delete("/api/web/sites/site-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_malformed_json(client):
    resp = await client.post(
        "/api/web/sites",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_rejects_malformed_json(client):
    resp = await client.put(
        "/api/web/sites/site-missing",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_rejects_oversized_content(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Big", "content": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_update_rejects_oversized_content(client):
    resp = await client.post(
        "/api/web/sites",
        json={"title": "Landing", "content": "{}"},
    )
    site_id = resp.json()["id"]

    resp = await client.put(
        f"/api/web/sites/{site_id}",
        json={"content": "x" * (MAX_CONTENT_BYTES + 1)},
    )
    assert resp.status_code == 413
