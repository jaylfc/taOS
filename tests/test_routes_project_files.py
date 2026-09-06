import io

import pytest


@pytest.mark.asyncio
async def test_list_unknown_slug_returns_empty(client):
    """Unknown slug auto-creates an empty folder and returns []."""
    resp = await client.get("/api/projects/unknown-slug/files")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_mkdir_then_list(client):
    """mkdir creates a folder that then appears in listing."""
    resp = await client.post("/api/projects/myproject/mkdir", json={"path": "docs"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "created"

    resp = await client.get("/api/projects/myproject/files")
    assert resp.status_code == 200
    names = [e["name"] for e in resp.json()]
    assert "docs" in names


@pytest.mark.asyncio
async def test_upload_then_list(client):
    """Uploaded file appears in listing with correct size."""
    content = b"hello project"
    resp = await client.post(
        "/api/projects/proj1/files/upload",
        files={"file": ("hello.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "hello.txt"
    assert body["size"] == len(content)

    resp = await client.get("/api/projects/proj1/files")
    assert resp.status_code == 200
    entries = resp.json()
    names = [e["name"] for e in entries]
    assert "hello.txt" in names
    entry = next(e for e in entries if e["name"] == "hello.txt")
    assert entry["size"] == len(content)


@pytest.mark.asyncio
async def test_get_file_streams_bytes(client):
    """GET file returns the uploaded bytes."""
    content = b"stream me"
    await client.post(
        "/api/projects/proj2/files/upload",
        files={"file": ("data.bin", io.BytesIO(content), "application/octet-stream")},
    )
    resp = await client.get("/api/projects/proj2/files/data.bin")
    assert resp.status_code == 200
    assert resp.content == content


@pytest.mark.asyncio
async def test_delete_removes_file(client):
    """Delete removes the file; listing is empty afterwards."""
    content = b"delete me"
    await client.post(
        "/api/projects/proj3/files/upload",
        files={"file": ("todelete.txt", io.BytesIO(content), "text/plain")},
    )

    resp = await client.delete("/api/projects/proj3/files/todelete.txt")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    resp = await client.get("/api/projects/proj3/files")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.parametrize("bad_slug", ["", "a/b", "..", "."])
@pytest.mark.asyncio
async def test_invalid_slug_returns_4xx(client, bad_slug):
    """Empty or path-traversal slugs are rejected (400 from our handler, or 404/422 at routing layer)."""
    resp = await client.get(f"/api/projects/{bad_slug}/files")
    assert resp.status_code in (400, 404, 422)


@pytest.mark.asyncio
async def test_path_traversal_returns_400(client):
    """?path=../../etc/passwd is rejected by _resolve_safe."""
    resp = await client.get("/api/projects/safe-proj/files?path=../../etc/passwd")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_stats_empty_project(client):
    """Stats for a brand-new project folder returns 0 files and 0 bytes."""
    resp = await client.get("/api/projects/empty-proj/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_files"] == 0
    assert body["total_size"] == 0


@pytest.mark.asyncio
async def test_upload_to_existing_file_path_returns_400(client):
    """Uploading with a path= that points to an existing file returns 400."""
    content = b"i am a file"
    # First upload creates the file at the root
    await client.post(
        "/api/projects/collision-proj/files/upload",
        files={"file": ("blocker.txt", io.BytesIO(content), "text/plain")},
    )
    # Now try to upload into that file as if it were a directory
    resp = await client.post(
        "/api/projects/collision-proj/files/upload?path=blocker.txt",
        files={"file": ("inner.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert resp.status_code == 400
    assert "conflict" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_mkdir_at_existing_file_path_returns_400(client):
    """mkdir at a path that already holds a file returns 400."""
    content = b"i am a file"
    await client.post(
        "/api/projects/mkdir-collision/files/upload",
        files={"file": ("blocker.txt", io.BytesIO(content), "text/plain")},
    )
    resp = await client.post(
        "/api/projects/mkdir-collision/mkdir",
        json={"path": "blocker.txt"},
    )
    assert resp.status_code == 400
    assert "conflict" in resp.json()["error"].lower()


@pytest.mark.asyncio
async def test_stats_after_upload(client):
    """Stats reflect uploaded files."""
    content = b"x" * 100
    await client.post(
        "/api/projects/stats-proj/files/upload",
        files={"file": ("a.txt", io.BytesIO(content), "text/plain")},
    )
    resp = await client.get("/api/projects/stats-proj/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_files"] == 1
    assert body["total_size"] == 100
