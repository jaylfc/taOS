"""Tests for the built-in Photos location of the Files app (tsk-cbop43).

The phone camera writes to ``~/Pictures/taOS`` of the account the controller
runs as, which sits outside every root the Files app lists today. Photos is a
built-in location on that folder: listed only when the directory exists, and
served by the same listing, download and delete code, and the same path
traversal guard, as the workspace locations. The tests below assert that parity
against ``/api/workspace/files`` wherever the card asks for it.
"""
import io

import pytest
from httpx import ASGITransport, AsyncClient


def _photos_dir(monkeypatch, tmp_path):
    """Point the built-in location at a temp Pictures directory."""
    base = tmp_path / "Pictures"
    monkeypatch.setenv("TAOS_PHOTOS_DIR", str(base))
    return base


class TestPhotosLocationRoutes:

    @pytest.mark.asyncio
    async def test_location_absent_when_directory_missing(self, client, monkeypatch, tmp_path):
        """No Pictures folder means no Photos entry."""
        _photos_dir(monkeypatch, tmp_path)
        resp = await client.get("/api/files/locations")
        assert resp.status_code == 200
        assert [entry["id"] for entry in resp.json()] == []

    @pytest.mark.asyncio
    async def test_location_listed_when_directory_exists(self, client, monkeypatch, tmp_path):
        """An existing Pictures folder is listed as the Photos location."""
        _photos_dir(monkeypatch, tmp_path).mkdir()
        resp = await client.get("/api/files/locations")
        assert resp.status_code == 200
        assert {"id": "photos", "label": "Photos"} in resp.json()

    @pytest.mark.asyncio
    async def test_lists_files_in_the_directory(self, client, monkeypatch, tmp_path):
        """Listing returns the folder's contents in the workspace listing shape."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        (base / "IMG_0001.jpg").write_bytes(b"jpeg-bytes")
        (base / "shots").mkdir()

        resp = await client.get("/api/photos/files")
        assert resp.status_code == 200
        entries = resp.json()
        by_name = {e["name"]: e for e in entries}
        assert set(by_name) == {"IMG_0001.jpg", "shots"}
        assert by_name["shots"]["is_dir"] is True
        assert by_name["IMG_0001.jpg"]["path"] == "IMG_0001.jpg"
        assert by_name["IMG_0001.jpg"]["size"] == len(b"jpeg-bytes")

    @pytest.mark.asyncio
    async def test_download_and_thumbnail_path_serves_the_file(self, client, monkeypatch, tmp_path):
        """GET /api/photos/files/<path> streams the file (download and thumbnail)."""
        base = _photos_dir(monkeypatch, tmp_path)
        (base / "sub").mkdir(parents=True)
        (base / "sub" / "IMG_0002.jpg").write_bytes(b"thumbnail-bytes")

        resp = await client.get("/api/photos/files/sub/IMG_0002.jpg")
        assert resp.status_code == 200
        assert resp.content == b"thumbnail-bytes"

    @pytest.mark.asyncio
    async def test_delete_removes_the_file(self, client, monkeypatch, tmp_path):
        """Deleting a photo removes it from the folder, as the other locations do."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        target = base / "IMG_0003.jpg"
        target.write_bytes(b"bye")

        resp = await client.delete("/api/photos/files/IMG_0003.jpg")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert not target.exists()
        listing = await client.get("/api/photos/files")
        assert [e["name"] for e in listing.json()] == []

    @pytest.mark.asyncio
    async def test_delete_missing_file_returns_404(self, client, monkeypatch, tmp_path):
        _photos_dir(monkeypatch, tmp_path).mkdir()
        resp = await client.delete("/api/photos/files/ghost.jpg")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_traversal_refused_as_in_the_workspace_location(
        self, client, monkeypatch, tmp_path
    ):
        """``..``, an absolute path and a symlink out of the base are refused with
        the SAME status the user workspace location refuses them with."""
        base = _photos_dir(monkeypatch, tmp_path)
        base.mkdir()
        # The user workspace root is created by its first request.
        await client.post(
            "/api/workspace/files/upload",
            files={"file": ("anchor.txt", io.BytesIO(b"anchor"), "text/plain")},
        )
        workspace = tmp_path / "workspace"
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"outside")
        # A symlink that resolves outside the base, in both locations.
        (base / "escape.txt").symlink_to(outside)
        (workspace / "escape.txt").symlink_to(outside)

        spellings = [
            ("dotdot_query", lambda loc: f"/api/{loc}/files?path=../../etc"),
            ("dotdot_path", lambda loc: f"/api/{loc}/files/%2e%2e/etc/hostname"),
            ("absolute", lambda loc: f"/api/{loc}/files/%2Fetc%2Fhostname"),
            ("symlink_out", lambda loc: f"/api/{loc}/files/escape.txt"),
        ]
        for name, build in spellings:
            workspace_status = (await client.get(build("workspace"))).status_code
            resp = await client.get(build("photos"))
            assert resp.status_code == 400, f"{name}: {resp.status_code}"
            assert resp.status_code == workspace_status, name

    @pytest.mark.asyncio
    async def test_unauthenticated_requests_refused(self, client, app, monkeypatch, tmp_path):
        """Same authorization as the other locations: a session is required."""
        _photos_dir(monkeypatch, tmp_path).mkdir()
        anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        try:
            for url in ("/api/files/locations", "/api/photos/files"):
                resp = await anon.get(url)
                assert resp.status_code == 401, url
            resp = await anon.delete("/api/photos/files/IMG_0001.jpg")
            assert resp.status_code == 401
        finally:
            await anon.aclose()
