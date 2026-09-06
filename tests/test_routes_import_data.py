import io
import pytest


class TestImportDataRoutes:
    @pytest.mark.asyncio
    async def test_upload_text_file(self, client):
        file_content = b"This is a test document for import."
        resp = await client.post(
            "/api/import/upload",
            files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_embed_files(self, client):
        # Upload a file first, then embed it
        content = b"Content to embed for import data test."
        await client.post(
            "/api/import/upload",
            files={"file": ("import_data_test.txt", io.BytesIO(content), "text/plain")},
        )
        resp = await client.post("/api/import/embed", json={
            "agent_name": "test-agent",
            "files": ["import_data_test.txt"],
        })
        assert resp.status_code == 200
        assert "status" in resp.json()

    @pytest.mark.asyncio
    async def test_embed_files_returns_embedded_field(self, client, monkeypatch):
        # Upload a file first
        content = b"Content to check embedded field in response."
        await client.post(
            "/api/import/upload",
            files={"file": ("embed_field_test.txt", io.BytesIO(content), "text/plain")},
        )

        # Force qmd serve POST /ingest to raise so we can verify the
        # failure path reports embedded: False. In the happy path the
        # route round-trips to the host qmd.service /ingest endpoint
        # and persists the chunk into the per-agent SQLite under
        # data/agent-memory/{agent_name}/index.sqlite.
        from unittest.mock import AsyncMock, MagicMock
        app = client._transport.app
        mock_http = MagicMock()
        async def _post(*_a, **_kw):
            raise RuntimeError("qmd unreachable")
        mock_http.post = AsyncMock(side_effect=_post)
        mock_http.aclose = AsyncMock(return_value=None)
        monkeypatch.setattr(app.state, "http_client", mock_http)

        resp = await client.post("/api/import/embed", json={
            "agent_name": "test-agent",
            "files": ["embed_field_test.txt"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "embedded" in data
        # Ingest call raised → no file got embedded
        assert data["embedded"] is False
        assert len(data["files"]) == 1
        assert "embedded" in data["files"][0]
        assert data["files"][0]["embedded"] is False

    @pytest.mark.asyncio
    async def test_upload_no_file(self, client):
        resp = await client.post("/api/import/upload")
        assert resp.status_code in (400, 422)
