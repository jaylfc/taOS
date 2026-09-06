from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


class TestTrailingSlashRedirect:
    @pytest.mark.asyncio
    async def test_redirect_no_slash_returns_307(self, client):
        resp = await client.get("/apps/myapp", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/apps/myapp/"


class TestServiceProxyErrors:
    @pytest.mark.asyncio
    async def test_returns_503_when_installed_apps_missing(self, client, monkeypatch):
        monkeypatch.setattr(client._transport.app.state, "installed_apps", None)
        resp = await client.get("/apps/myapp/")
        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data
        assert "Service registry unavailable" in data["error"]

    @pytest.mark.asyncio
    async def test_returns_404_for_not_installed_app(self, client, monkeypatch):
        mock_store = MagicMock()
        mock_store.get_runtime_location = AsyncMock(return_value=None)
        mock_store.is_installed = AsyncMock(return_value=False)
        monkeypatch.setattr(client._transport.app.state, "installed_apps", mock_store)
        resp = await client.get("/apps/unknown-app/")
        assert resp.status_code == 404
        data = resp.json()
        assert "error" in data
        assert "not installed" in data["error"]

    @pytest.mark.asyncio
    async def test_returns_503_when_no_runtime_location(self, client, monkeypatch):
        mock_store = MagicMock()
        mock_store.get_runtime_location = AsyncMock(return_value=None)
        mock_store.is_installed = AsyncMock(return_value=True)
        monkeypatch.setattr(client._transport.app.state, "installed_apps", mock_store)
        resp = await client.get("/apps/myapp/")
        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data
        assert "no runtime location" in data["error"]
