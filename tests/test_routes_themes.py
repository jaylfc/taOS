"""Endpoint tests for tinyagentos/routes/themes.py."""

from __future__ import annotations

import io
import zipfile

import pytest


def _make_theme_package(theme_id="test-theme", name="Test Theme", version="1.0.0",
                        extra_files=None):
    """Return bytes of a valid .taostheme (zip) with a minimal theme.yaml."""
    manifest = (
        f"id: {theme_id}\n"
        f"name: {name}\n"
        f"version: {version}\n"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("theme.yaml", manifest)
        if extra_files:
            for fname, content in extra_files.items():
                zf.writestr(fname, content)
    return buf.getvalue()


class TestThemesRoutes:
    @pytest.mark.asyncio
    async def test_list_themes_returns_200_and_list(self, client):
        resp = await client.get("/api/themes")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_list_themes_empty_when_none_installed(self, client):
        data = (await client.get("/api/themes")).json()
        assert data == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_theme_returns_200_with_removed_false(self, client):
        resp = await client.delete("/api/themes/nonexistent-theme-xyz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] is False

    @pytest.mark.asyncio
    async def test_install_theme_empty_body_returns_422(self, client):
        resp = await client.post("/api/themes/install")
        assert resp.status_code == 422

    # -- POST /api/themes/install --

    @pytest.mark.asyncio
    async def test_install_theme_returns_200_and_theme_id(self, client):
        pkg = _make_theme_package()
        resp = await client.post(
            "/api/themes/install",
            files={"package": ("test.taostheme", pkg, "application/zip")},
        )
        assert resp.status_code == 200
        assert resp.json()["theme_id"] == "test-theme"

    @pytest.mark.asyncio
    async def test_install_theme_appears_in_list(self, client):
        pkg = _make_theme_package(theme_id="listed-theme")
        await client.post(
            "/api/themes/install",
            files={"package": ("listed.taostheme", pkg, "application/zip")},
        )
        data = (await client.get("/api/themes")).json()
        ids = [t["theme_id"] for t in data]
        assert "listed-theme" in ids

    @pytest.mark.asyncio
    async def test_install_invalid_zip_returns_400(self, client):
        resp = await client.post(
            "/api/themes/install",
            files={"package": ("bad.taostheme", b"not a zip file", "application/zip")},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_install_missing_theme_yaml_returns_400(self, client):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("other.txt", "no theme.yaml here")
        resp = await client.post(
            "/api/themes/install",
            files={"package": ("no-yaml.taostheme", buf.getvalue(), "application/zip")},
        )
        assert resp.status_code == 400

    # -- DELETE /api/themes/{theme_id} --

    @pytest.mark.asyncio
    async def test_remove_installed_theme_returns_removed_true(self, client):
        pkg = _make_theme_package(theme_id="removable-theme")
        await client.post(
            "/api/themes/install",
            files={"package": ("removable.taostheme", pkg, "application/zip")},
        )
        resp = await client.delete("/api/themes/removable-theme")
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] is True
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_remove_theme_disappears_from_list(self, client):
        pkg = _make_theme_package(theme_id="gone-theme")
        await client.post(
            "/api/themes/install",
            files={"package": ("gone.taostheme", pkg, "application/zip")},
        )
        await client.delete("/api/themes/gone-theme")
        data = (await client.get("/api/themes")).json()
        ids = [t["theme_id"] for t in data]
        assert "gone-theme" not in ids

    @pytest.mark.asyncio
    async def test_delete_invalid_theme_id_returns_404(self, client):
        resp = await client.delete("/api/themes/bad.theme")
        assert resp.status_code == 404

    # -- GET /api/themes/{theme_id}/assets/{path:path} --

    @pytest.mark.asyncio
    async def test_theme_asset_returns_file_content(self, client, app):
        themes_root = app.state.data_dir / "themes"
        asset_dir = themes_root / "asset-theme" / "assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / "style.css").write_text("body { color: red; }")
        resp = await client.get("/api/themes/asset-theme/assets/style.css")
        assert resp.status_code == 200
        assert resp.text == "body { color: red; }"

    @pytest.mark.asyncio
    async def test_theme_asset_missing_file_returns_404(self, client, app):
        themes_root = app.state.data_dir / "themes"
        (themes_root / "asset-theme2" / "assets").mkdir(parents=True, exist_ok=True)
        resp = await client.get("/api/themes/asset-theme2/assets/missing.css")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_theme_asset_invalid_theme_id_returns_404(self, client):
        resp = await client.get("/api/themes/bad.theme/assets/style.css")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_theme_asset_nested_path_returns_file(self, client, app):
        themes_root = app.state.data_dir / "themes"
        nested = themes_root / "nested-theme" / "assets" / "sub" / "deep"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        resp = await client.get("/api/themes/nested-theme/assets/sub/deep/icon.png")
        assert resp.status_code == 200
        assert resp.content == b"\x89PNG\r\n\x1a\n"
