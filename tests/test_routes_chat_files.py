"""Endpoint tests for tinyagentos/routes/chat_files.py."""
from __future__ import annotations

import pytest
from unittest.mock import patch


class TestAttachmentFromPath:
    """Tests for POST /api/chat/attachments/from-path."""

    @pytest.mark.asyncio
    async def test_happy_path_workspace(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir
        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        test_file = ws_dir / "notes.txt"
        test_file.write_text("# hello")

        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/notes.txt",
            "source": "workspace",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "notes.txt"
        assert body["source"] == "workspace"
        assert body["mime_type"] == "text/plain"
        assert body["size"] == len("# hello")
        assert body["url"].startswith("/api/chat/files/")

    @pytest.mark.asyncio
    async def test_happy_path_agent_workspace(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir
        ws_dir = data_dir / "agent-workspaces" / "agent1"
        ws_dir.mkdir(parents=True, exist_ok=True)
        test_file = ws_dir / "report.txt"
        test_file.write_text("agent report content")

        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/agent1/report.txt",
            "source": "agent-workspace",
            "slug": "agent1",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "report.txt"
        assert body["source"] == "agent-workspace"

    @pytest.mark.asyncio
    async def test_missing_path_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "source": "workspace",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_invalid_source_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/foo.md",
            "source": "invalid-source",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_file_not_found_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/nonexistent.md",
            "source": "workspace",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_path_traversal_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/user/../../etc/passwd",
            "source": "workspace",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_workspace_source_requires_user_owner(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/other/file.md",
            "source": "workspace",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_agent_workspace_slug_mismatch_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/agent1/file.md",
            "source": "agent-workspace",
            "slug": "different-agent",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_agent_workspace_missing_slug_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/workspaces/agent1/file.md",
            "source": "agent-workspace",
        })
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_file_too_large_returns_413(self, client):
        app = client._transport.app
        data_dir = app.state.data_dir
        ws_dir = data_dir / "agent-workspaces" / "user"
        ws_dir.mkdir(parents=True, exist_ok=True)
        test_file = ws_dir / "big.bin"
        test_file.write_bytes(b"x" * 1024)

        with patch(
            "tinyagentos.routes.chat_files._MAX_ATTACHMENT_BYTES", 100
        ):
            r = await client.post("/api/chat/attachments/from-path", json={
                "path": "/workspaces/user/big.bin",
                "source": "workspace",
            })
        assert r.status_code == 413
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_empty_body_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={})
        assert r.status_code == 400
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_path_not_starting_with_workspaces_returns_400(self, client):
        r = await client.post("/api/chat/attachments/from-path", json={
            "path": "/etc/passwd",
            "source": "workspace",
        })
        assert r.status_code == 400
        assert "error" in r.json()


class TestUploadFile:
    """Tests for POST /api/chat/upload."""

    @pytest.mark.asyncio
    async def test_happy_path(self, client):
        content = b"file content here"
        r = await client.post(
            "/api/chat/upload",
            files={"file": ("test.txt", content, "text/plain")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "test.txt"
        assert body["content_type"] == "text/plain"
        assert body["size"] == len(content)
        assert body["url"].startswith("/api/chat/files/")
        assert "id" in body

    @pytest.mark.asyncio
    async def test_happy_path_with_channel_id(self, client):
        content = b"some data"
        r = await client.post(
            "/api/chat/upload",
            files={"file": ("data.csv", content, "text/csv")},
            data={"channel_id": "chan-123"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["filename"] == "data.csv"
        assert body["size"] == len(content)

    @pytest.mark.asyncio
    async def test_file_too_large_returns_413(self, client):
        content = b"x" * 1024
        with patch(
            "tinyagentos.routes.chat_files._MAX_ATTACHMENT_BYTES", 100
        ):
            r = await client.post(
                "/api/chat/upload",
                files={"file": ("big.bin", content, "application/octet-stream")},
            )
        assert r.status_code == 413
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_no_filename_returns_422(self, client):
        content = b"no name content"
        r = await client.post(
            "/api/chat/upload",
            files={"file": ("", content, "application/octet-stream")},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_file_is_stored_on_disk(self, client):
        content = b"stored content check"
        r = await client.post(
            "/api/chat/upload",
            files={"file": ("stored.txt", content, "text/plain")},
        )
        assert r.status_code == 200
        body = r.json()
        app = client._transport.app
        file_path = app.state.data_dir / "chat-files" / body["url"].split("/")[-1]
        assert file_path.exists()
        assert file_path.read_bytes() == content


class TestServeFile:
    """Tests for GET /api/chat/files/{filename}."""

    @pytest.mark.asyncio
    async def test_happy_path(self, client):
        app = client._transport.app
        chat_files = app.state.data_dir / "chat-files"
        chat_files.mkdir(parents=True, exist_ok=True)
        stored = chat_files / "testfile.txt"
        stored.write_text("served content")

        r = await client.get("/api/chat/files/testfile.txt")
        assert r.status_code == 200
        assert r.text == "served content"

    @pytest.mark.asyncio
    async def test_file_not_found_returns_404(self, client):
        r = await client.get("/api/chat/files/nonexistent.txt")
        assert r.status_code == 404
        assert "error" in r.json()

    @pytest.mark.asyncio
    async def test_path_traversal_returns_404(self, client):
        r = await client.get("/api/chat/files/..%2F..%2Fetc%2Fpasswd")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_binary_file_served(self, client):
        app = client._transport.app
        chat_files = app.state.data_dir / "chat-files"
        chat_files.mkdir(parents=True, exist_ok=True)
        binary_data = bytes(range(256))
        stored = chat_files / "image.png"
        stored.write_bytes(binary_data)

        r = await client.get("/api/chat/files/image.png")
        assert r.status_code == 200
        assert r.content == binary_data
