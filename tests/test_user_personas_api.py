import pytest


@pytest.mark.asyncio
async def test_create_list_get_update_delete_persona(client):
    # Create
    r1 = await client.post("/api/user-personas", json={"name": "X", "soul_md": "S"})
    assert r1.status_code == 201
    pid = r1.json()["id"]
    assert pid

    # List — persona appears newest-first
    r2 = await client.get("/api/user-personas")
    assert r2.status_code == 200
    assert any(p["id"] == pid for p in r2.json()["personas"])

    # Get
    r3 = await client.get(f"/api/user-personas/{pid}")
    assert r3.status_code == 200
    data = r3.json()
    assert data["id"] == pid
    assert data["name"] == "X"
    assert data["soul_md"] == "S"

    # Update
    r4 = await client.patch(f"/api/user-personas/{pid}", json={"name": "Y", "agent_md": "A"})
    assert r4.status_code == 200
    updated = (await client.get(f"/api/user-personas/{pid}")).json()
    assert updated["name"] == "Y"
    assert updated["agent_md"] == "A"
    assert updated["soul_md"] == "S"  # unchanged

    # Delete
    r5 = await client.delete(f"/api/user-personas/{pid}")
    assert r5.status_code == 200
    assert r5.json()["ok"] is True

    # Gone after delete
    r6 = await client.get(f"/api/user-personas/{pid}")
    assert r6.status_code == 404


@pytest.mark.asyncio
async def test_get_unknown_returns_404(client):
    r = await client.get("/api/user-personas/doesnotexist")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_unknown_returns_404(client):
    r = await client.patch("/api/user-personas/doesnotexist", json={"name": "Z"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_is_idempotent(client):
    r = await client.delete("/api/user-personas/doesnotexist")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.asyncio
async def test_list_empty(client):
    r = await client.get("/api/user-personas")
    assert r.status_code == 200
    assert r.json()["personas"] == []


@pytest.mark.asyncio
async def test_create_optional_fields(client):
    r = await client.post("/api/user-personas", json={"name": "Minimal"})
    assert r.status_code == 201
    pid = r.json()["id"]
    persona = (await client.get(f"/api/user-personas/{pid}")).json()
    assert persona["soul_md"] == ""
    assert persona["agent_md"] == ""
    assert persona["description"] is None
