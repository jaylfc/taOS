import pytest


@pytest.mark.asyncio
async def test_create_list_get_update_delete_song(client):
    resp = await client.post(
        "/api/songs",
        json={"name": "My Track", "content": '{"tempo":92,"tracks":[]}'},
    )
    assert resp.status_code == 200
    created = resp.json()
    song_id = created["id"]
    assert created["name"] == "My Track"
    assert created["content"] == '{"tempo":92,"tracks":[]}'
    assert isinstance(created["updated_at"], int)

    resp = await client.get("/api/songs")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == song_id
    assert "content" not in items[0]

    resp = await client.get(f"/api/songs/{song_id}")
    assert resp.status_code == 200
    assert resp.json()["content"] == '{"tempo":92,"tracks":[]}'

    resp = await client.put(
        f"/api/songs/{song_id}",
        json={"name": "Renamed", "content": '{"tempo":100,"tracks":[]}'},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "Renamed"
    assert updated["content"] == '{"tempo":100,"tracks":[]}'
    assert updated["updated_at"] >= created["updated_at"]

    resp = await client.delete(f"/api/songs/{song_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get(f"/api/songs/{song_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_missing_name(client):
    resp = await client.post(
        "/api/songs",
        json={"name": "   ", "content": ""},
    )
    assert resp.status_code == 400
    assert "name" in resp.json()["error"]


@pytest.mark.asyncio
async def test_create_rejects_non_string_content(client):
    resp = await client.post(
        "/api/songs",
        json={"name": "X", "content": 42},
    )
    assert resp.status_code == 400
    assert "content" in resp.json()["error"]


@pytest.mark.asyncio
async def test_get_missing_song_returns_404(client):
    resp = await client.get("/api/songs/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_song_returns_404(client):
    resp = await client.put(
        "/api/songs/does-not-exist",
        json={"name": "Nope"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_oversize_content(client):
    resp = await client.post(
        "/api/songs",
        json={"name": "Big", "content": "x" * (5 * 1024 * 1024 + 1)},
    )
    assert resp.status_code == 413
    assert "content" in resp.json()["error"]


@pytest.mark.asyncio
async def test_update_rejects_oversize_content(client):
    resp = await client.post("/api/songs", json={"name": "S", "content": "{}"})
    song_id = resp.json()["id"]

    resp = await client.put(
        f"/api/songs/{song_id}",
        json={"content": "x" * (5 * 1024 * 1024 + 1)},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_delete_missing_song_returns_404(client):
    resp = await client.delete("/api/songs/missing-id")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_persists_content(client):
    resp = await client.post(
        "/api/songs",
        json={"name": "Doc", "content": "body"},
    )
    song_id = resp.json()["id"]

    resp = await client.put(
        f"/api/songs/{song_id}",
        json={"content": "revised body"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "revised body"

    # The change is persisted, not just echoed.
    resp = await client.get(f"/api/songs/{song_id}")
    assert resp.json()["content"] == "revised body"
