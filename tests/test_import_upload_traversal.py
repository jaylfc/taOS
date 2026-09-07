"""GHSA-rwrp-hfc4-qg2w: /api/import/upload and /api/import/embed built paths
from client-supplied names with ``UPLOAD_DIR / name``. ``pathlib`` drops the
left operand for an absolute right operand, so any authenticated user could
write (upload) or read (embed) any file the server process can reach. Every
name must resolve INSIDE UPLOAD_DIR or be refused with a 400 before the
filesystem is touched."""

import io
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.routes import import_data as mod


def _mock_http(monkeypatch, client):
    http = MagicMock()
    http.post = AsyncMock()
    http.aclose = AsyncMock(return_value=None)
    monkeypatch.setattr(client._transport.app.state, "http_client", http)
    return http


class TestUploadTraversal:
    @pytest.mark.asyncio
    async def test_absolute_filename_is_refused_and_not_written(self, client, tmp_path):
        target = tmp_path / "auth_user.json"
        resp = await client.post(
            "/api/import/upload",
            files={"file": (str(target), io.BytesIO(b'{"users": []}'), "application/json")},
        )
        assert resp.status_code == 400, resp.text
        assert not target.exists(), "upload escaped UPLOAD_DIR via an absolute filename"

    @pytest.mark.asyncio
    async def test_dotdot_filename_is_refused_and_not_written(self, client):
        escaped = mod.UPLOAD_DIR.parent / "traversal_probe.txt"
        escaped.unlink(missing_ok=True)
        resp = await client.post(
            "/api/import/upload",
            files={"file": ("../traversal_probe.txt", io.BytesIO(b"x"), "text/plain")},
        )
        try:
            assert resp.status_code == 400, resp.text
            assert not escaped.exists(), "upload escaped UPLOAD_DIR via ../"
        finally:
            escaped.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_hidden_filename_is_refused(self, client):
        resp = await client.post(
            "/api/import/upload",
            files={"file": (".auth_user.json", io.BytesIO(b"{}"), "application/json")},
        )
        assert resp.status_code == 400, resp.text

    @pytest.mark.asyncio
    async def test_plain_basename_still_uploads_inside_upload_dir(self, client):
        resp = await client.post(
            "/api/import/upload",
            files={"file": ("plain_ok.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["path"] == str(mod.UPLOAD_DIR / "plain_ok.txt")


class TestEmbedTraversal:
    @pytest.mark.asyncio
    async def test_absolute_path_in_files_is_refused_before_read(self, client, tmp_path, monkeypatch):
        secret = tmp_path / "secret.txt"
        secret.write_text("do not read me")
        http = _mock_http(monkeypatch, client)
        resp = await client.post(
            "/api/import/embed",
            json={"agent_name": "test-agent", "files": [str(secret)]},
        )
        assert resp.status_code == 400, resp.text
        http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_traversal_agent_name_is_refused(self, client, monkeypatch):
        http = _mock_http(monkeypatch, client)
        await client.post(
            "/api/import/upload",
            files={"file": ("agent_name_probe.txt", io.BytesIO(b"x"), "text/plain")},
        )
        resp = await client.post(
            "/api/import/embed",
            json={"agent_name": "../../escaped", "files": ["agent_name_probe.txt"]},
        )
        assert resp.status_code == 400, resp.text
        http.post.assert_not_called()
