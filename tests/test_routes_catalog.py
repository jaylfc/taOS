"""Endpoint tests for tinyagentos/routes/catalog.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
class TestCatalogStats:
    async def test_stats_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.stats = AsyncMock(return_value={
            "total_sessions": 0,
            "total_sub_sessions": 0,
            "days_cataloged": 0,
        })
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/stats")
        assert resp.status_code == 200

    async def test_stats_returns_dict(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.stats = AsyncMock(return_value={
            "total_sessions": 1,
            "total_sub_sessions": 2,
            "days_cataloged": 3,
        })
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (await client.get("/api/memory/catalog/stats")).json()
        assert isinstance(data, dict)

    async def test_stats_exception_returns_500(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.stats = AsyncMock(side_effect=RuntimeError("db locked"))
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/stats")
        assert resp.status_code == 500
        data = resp.json()
        assert "error" in data


@pytest.mark.asyncio
class TestCatalogDate:
    async def test_date_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.lookup_date = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/date/2026-06-19")
        assert resp.status_code == 200

    async def test_date_returns_list(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.lookup_date = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (await client.get("/api/memory/catalog/date/2026-06-19")).json()
        assert isinstance(data, list)


@pytest.mark.asyncio
class TestCatalogRange:
    async def test_range_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.lookup_range = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get(
            "/api/memory/catalog/range",
            params={"start": "2026-06-01", "end": "2026-06-30"},
        )
        assert resp.status_code == 200

    async def test_range_returns_list(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.lookup_range = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (
            await client.get(
                "/api/memory/catalog/range",
                params={"start": "2026-06-01", "end": "2026-06-30"},
            )
        ).json()
        assert isinstance(data, list)

    async def test_range_missing_params_returns_422(self, client):
        resp = await client.get("/api/memory/catalog/range")
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestCatalogSearch:
    async def test_search_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.search_topic = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/search", params={"q": "test"})
        assert resp.status_code == 200

    async def test_search_returns_list(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.search_topic = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (
            await client.get(
                "/api/memory/catalog/search", params={"q": "test"}
            )
        ).json()
        assert isinstance(data, list)

    async def test_search_missing_q_returns_422(self, client):
        resp = await client.get("/api/memory/catalog/search")
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestCatalogSession:
    async def test_session_not_found_returns_404(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.get_session = AsyncMock(return_value=None)
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/session/99999")
        assert resp.status_code == 404

    async def test_session_not_found_error_shape(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.get_session = AsyncMock(return_value=None)
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (await client.get("/api/memory/catalog/session/99999")).json()
        assert "detail" in data

    async def test_session_found_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.get_session = AsyncMock(return_value={"id": 1, "title": "test"})
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/session/1")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data


@pytest.mark.asyncio
class TestCatalogSessionContext:
    async def test_context_not_found_returns_404(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.get_session_context = AsyncMock(return_value=None)
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/session/99999/context")
        assert resp.status_code == 404

    async def test_context_not_found_error_shape(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.get_session_context = AsyncMock(return_value=None)
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (
            await client.get("/api/memory/catalog/session/99999/context")
        ).json()
        assert "detail" in data

    async def test_context_found_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.get_session_context = AsyncMock(
            return_value={"session_id": 1, "context": "test"}
        )
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/session/1/context")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data


@pytest.mark.asyncio
class TestCatalogRecent:
    async def test_recent_returns_200(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.recent = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        resp = await client.get("/api/memory/catalog/recent")
        assert resp.status_code == 200

    async def test_recent_returns_list(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.recent = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (await client.get("/api/memory/catalog/recent")).json()
        assert isinstance(data, list)

    async def test_recent_respects_limit(self, client, monkeypatch):
        catalog = MagicMock()
        catalog.recent = AsyncMock(return_value=[])
        monkeypatch.setattr(
            client._transport.app.state, "session_catalog", catalog, raising=False
        )
        data = (
            await client.get("/api/memory/catalog/recent", params={"limit": 5})
        ).json()
        assert isinstance(data, list)
        assert len(data) <= 5


# POST /api/memory/catalog/index and POST /api/memory/catalog/rebuild are
# skipped: they create a CatalogPipeline that depends on archive files and
# potentially an LLM service, which are not available in the test fixture.
