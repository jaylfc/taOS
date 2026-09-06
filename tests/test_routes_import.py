import io

import pytest


@pytest.mark.asyncio
class TestImportPage:
    async def test_upload_text_file(self, client):
        content = b"Hello, this is test content for embedding."
        resp = await client.post(
            "/api/import/upload",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "uploaded"
        assert data["filename"] == "test.txt"
        assert data["size"] == len(content)

    async def test_upload_markdown_file(self, client):
        content = b"# Test Document\n\nSome markdown content."
        resp = await client.post(
            "/api/import/upload",
            files={"file": ("notes.md", io.BytesIO(content), "text/markdown")},
        )
        assert resp.status_code == 200
        assert resp.json()["filename"] == "notes.md"

    async def test_upload_unsupported_format(self, client):
        resp = await client.post(
            "/api/import/upload",
            files={"file": ("image.png", io.BytesIO(b"\x89PNG"), "image/png")},
        )
        assert resp.status_code == 400
        assert "Unsupported" in resp.json()["error"]

    async def test_embed_files(self, client):
        # Upload a file first
        content = b"Content to embed"
        await client.post(
            "/api/import/upload",
            files={"file": ("embed_me.txt", io.BytesIO(content), "text/plain")},
        )
        # Trigger embedding
        resp = await client.post("/api/import/embed", json={
            "agent_name": "test-agent",
            "files": ["embed_me.txt"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "embedded"
        assert data["count"] == 1
        assert data["agent_name"] == "test-agent"

    async def test_embed_missing_agent(self, client):
        resp = await client.post("/api/import/embed", json={
            "agent_name": "",
            "files": ["test.txt"],
        })
        assert resp.status_code == 400

    async def test_embed_no_files(self, client):
        resp = await client.post("/api/import/embed", json={
            "agent_name": "test-agent",
            "files": [],
        })
        assert resp.status_code == 400

    async def test_embed_missing_files(self, client):
        resp = await client.post("/api/import/embed", json={
            "agent_name": "test-agent",
            "files": ["nonexistent.txt"],
        })
        assert resp.status_code == 404
