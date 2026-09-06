"""Tests for popularity wiring on the Store catalog + /api/store/popularity."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from tinyagentos import store_popularity as sp


@pytest.fixture(autouse=True)
def _clear_cache():
    sp._reset_cache_for_tests()
    yield
    sp._reset_cache_for_tests()


def _fake_app(app_id: str, homepage: str):
    a = MagicMock()
    a.id = app_id
    a.name = app_id
    a.type = "plugin"
    a.category = ""
    a.version = "1.0.0"
    a.description = ""
    a.icon = ""
    a.homepage = homepage
    a.requires = {}
    a.install = {}
    a.hardware_tiers = {}
    a.variants = []
    return a


def _patch_registry(app, apps):
    reg = MagicMock()
    reg.list_available = MagicMock(return_value=apps)
    reg.is_installed = MagicMock(return_value=False)
    app.state.registry = reg
    app.state.installation_state = None


def _seed_stars(repo: str, stars: int) -> None:
    """Pre-warm the cache the way the background warmer would."""
    sp._star_cache[repo] = (time.time() + 3600, stars)


def _explode_on_live_fetch(monkeypatch):
    """Make any live GitHub call blow up, so a request-path fetch would fail.

    The request path must read from cache only; if the catalog route ever
    awaited GitHub, this would raise and the test would fail.
    """
    async def _boom(*a, **k):
        raise AssertionError("request path must not call GitHub")

    monkeypatch.setattr(sp, "fetch_stars", _boom)


class TestStorePopularityRoute:
    @pytest.mark.asyncio
    async def test_catalog_surfaces_cached_stars(self, client, monkeypatch):
        app = client._transport.app
        _patch_registry(app, [_fake_app("jellyfin", "https://github.com/jellyfin/jellyfin")])
        _seed_stars("jellyfin/jellyfin", 35000)
        _explode_on_live_fetch(monkeypatch)

        res = await client.get("/api/store/catalog")
        assert res.status_code == 200
        entry = res.json()[0]
        assert entry["repo"] == "jellyfin/jellyfin"
        assert entry["stars"] == 35000
        assert entry["popularity"] == {
            "github_stars": 35000,
            "installs": None,
            "score": 35000.0,
        }

    @pytest.mark.asyncio
    async def test_catalog_returns_immediately_without_github(self, client, monkeypatch):
        # Cold cache: stars are null and NO GitHub call happens in the request
        # path (a live call would raise via _explode_on_live_fetch).
        app = client._transport.app
        _patch_registry(app, [_fake_app("jellyfin", "https://github.com/jellyfin/jellyfin")])
        _explode_on_live_fetch(monkeypatch)

        res = await client.get("/api/store/catalog")
        assert res.status_code == 200
        entry = res.json()[0]
        assert entry["repo"] == "jellyfin/jellyfin"
        assert entry["stars"] is None
        assert entry["popularity"]["github_stars"] is None
        assert entry["popularity"]["score"] == 0.0

    @pytest.mark.asyncio
    async def test_non_github_entry_gets_null_popularity(self, client, monkeypatch):
        app = client._transport.app
        _patch_registry(app, [_fake_app("excalidraw", "https://excalidraw.com")])
        _explode_on_live_fetch(monkeypatch)

        res = await client.get("/api/store/catalog")
        entry = res.json()[0]
        assert entry["repo"] is None
        assert entry["stars"] is None
        assert entry["popularity"]["github_stars"] is None
        assert entry["popularity"]["score"] == 0.0

    @pytest.mark.asyncio
    async def test_dedicated_popularity_endpoint(self, client, monkeypatch):
        app = client._transport.app
        _patch_registry(app, [_fake_app("jellyfin", "https://github.com/jellyfin/jellyfin")])
        _seed_stars("jellyfin/jellyfin", 12345)
        _explode_on_live_fetch(monkeypatch)

        res = await client.get("/api/store/popularity")
        assert res.status_code == 200
        body = res.json()
        assert body["jellyfin"]["github_stars"] == 12345
        assert body["jellyfin"]["installs"] is None
