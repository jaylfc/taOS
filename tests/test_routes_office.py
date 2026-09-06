import pytest


@pytest.mark.asyncio
async def test_create_list_get_update_delete_doc(client):
    resp = await client.post(
        "/api/office/docs",
        json={"kind": "write", "title": "Launch note", "content": "Hello world"},
    )
    assert resp.status_code == 200
    created = resp.json()
    doc_id = created["id"]
    assert created["kind"] == "write"
    assert created["title"] == "Launch note"
    assert created["content"] == "Hello world"
    assert isinstance(created["updated_at"], int)

    resp = await client.get("/api/office/docs")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["id"] == doc_id
    assert "content" not in items[0]

    resp = await client.get(f"/api/office/docs/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["content"] == "Hello world"

    resp = await client.put(
        f"/api/office/docs/{doc_id}",
        json={"title": "Updated", "content": "Revised body"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["title"] == "Updated"
    assert updated["content"] == "Revised body"
    assert updated["updated_at"] >= created["updated_at"]

    resp = await client.delete(f"/api/office/docs/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get(f"/api/office/docs/{doc_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_invalid_kind(client):
    resp = await client.post(
        "/api/office/docs",
        json={"kind": "spreadsheet", "title": "X", "content": ""},
    )
    assert resp.status_code == 400
    assert "kind" in resp.json()["error"]


@pytest.mark.asyncio
async def test_create_rejects_missing_title(client):
    resp = await client.post(
        "/api/office/docs",
        json={"kind": "write", "title": "   ", "content": ""},
    )
    assert resp.status_code == 400
    assert "title" in resp.json()["error"]


@pytest.mark.asyncio
async def test_create_rejects_non_string_content(client):
    resp = await client.post(
        "/api/office/docs",
        json={"kind": "write", "title": "X", "content": 42},
    )
    assert resp.status_code == 400
    assert "content" in resp.json()["error"]


@pytest.mark.asyncio
async def test_get_missing_doc_returns_404(client):
    resp = await client.get("/api/office/docs/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_missing_doc_returns_404(client):
    resp = await client.put(
        "/api/office/docs/does-not-exist",
        json={"title": "Nope"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_rejects_invalid_kind(client):
    resp = await client.post(
        "/api/office/docs",
        json={"kind": "write", "title": "Doc", "content": ""},
    )
    doc_id = resp.json()["id"]

    resp = await client.put(
        f"/api/office/docs/{doc_id}",
        json={"kind": "invalid"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_missing_doc_returns_404(client):
    resp = await client.delete("/api/office/docs/missing-id")
    assert resp.status_code == 404

@pytest.mark.asyncio
async def test_update_persists_valid_kind(client):
    resp = await client.post(
        "/api/office/docs",
        json={"kind": "write", "title": "Doc", "content": "body"},
    )
    doc_id = resp.json()["id"]

    resp = await client.put(
        f"/api/office/docs/{doc_id}",
        json={"kind": "calc", "title": "Doc", "content": "body"},
    )
    assert resp.status_code == 200
    assert resp.json()["kind"] == "calc"

    # The change is persisted, not just echoed.
    resp = await client.get(f"/api/office/docs/{doc_id}")
    assert resp.json()["kind"] == "calc"
