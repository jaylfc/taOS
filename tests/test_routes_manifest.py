import pytest


class TestManifestEndpoint:
    @pytest.mark.asyncio
    async def test_get_manifest_returns_200(self, client):
        resp = await client.get("/manifest?app=messages")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_manifest_returns_json_body(self, client):
        resp = await client.get("/manifest?app=messages")
        data = resp.json()
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_get_manifest_has_required_keys(self, client):
        resp = await client.get("/manifest?app=messages")
        data = resp.json()
        assert "name" in data
        assert "short_name" in data
        assert "start_url" in data
        assert "display" in data
        assert "icons" in data

    @pytest.mark.asyncio
    async def test_get_manifest_values_for_messages_app(self, client):
        resp = await client.get("/manifest?app=messages")
        data = resp.json()
        assert data["name"] == "taOS talk"
        assert data["short_name"] == "taOS talk"
        assert data["start_url"] == "/app.html?app=messages"
        assert data["id"] == "/app.html?app=messages"
        assert data["scope"] == "/"
        assert data["display"] == "standalone"
        assert data["theme_color"] == "#141415"
        assert data["background_color"] == "#141415"

    @pytest.mark.asyncio
    async def test_get_manifest_icons_structure(self, client):
        resp = await client.get("/manifest?app=messages")
        icons = resp.json()["icons"]
        assert len(icons) == 2
        assert icons[0]["src"] == "/static/icon-192.png"
        assert icons[0]["sizes"] == "192x192"
        assert icons[0]["type"] == "image/png"
        assert icons[1]["src"] == "/static/icon-512.png"
        assert icons[1]["sizes"] == "512x512"

    @pytest.mark.asyncio
    async def test_get_manifest_content_type(self, client):
        resp = await client.get("/manifest?app=messages")
        content_type = resp.headers["content-type"]
        assert "application/manifest+json" in content_type

    @pytest.mark.asyncio
    async def test_get_manifest_unknown_app_returns_404(self, client):
        resp = await client.get("/manifest?app=nonexistent")
        assert resp.status_code == 404
