"""Tests for /api/archive/* routes."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def _inject_archive(app):
    """Replace app.state.archive with a mock that has all async methods."""
    store = MagicMock()
    store.record = AsyncMock(return_value=1)
    store.query = AsyncMock(return_value=[])
    store.get_event = AsyncMock(return_value=None)
    store.stats = AsyncMock(return_value={"total": 0})
    store.daily_summary = AsyncMock(return_value={"date": None, "events": 0})
    store.export_day = AsyncMock(return_value=[])
    store.set_user_tracking = AsyncMock()
    store.user_tracking_enabled = False
    store.compress_old_files = AsyncMock(return_value=0)
    app.state.archive = store
    return store


# ---------------------------------------------------------------------------
# POST /api/archive/record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRecordEvent:
    async def test_record_returns_id(self, client):
        store = _inject_archive(client._transport.app)
        store.record.return_value = 42
        resp = await client.post("/api/archive/record", json={
            "event_type": "user_message",
            "data": {"text": "hi"},
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 42
        assert body["status"] == "recorded"

    async def test_record_skipped_when_tracking_disabled(self, client):
        store = _inject_archive(client._transport.app)
        store.record.return_value = -1
        resp = await client.post("/api/archive/record", json={
            "event_type": "user_message",
            "data": {},
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"

    async def test_record_rejects_missing_event_type(self, client):
        _inject_archive(client._transport.app)
        resp = await client.post("/api/archive/record", json={
            "data": {"text": "hi"},
        })
        assert resp.status_code == 422

    async def test_record_accepts_optional_fields(self, client):
        store = _inject_archive(client._transport.app)
        resp = await client.post("/api/archive/record", json={
            "event_type": "agent_action",
            "data": {"action": "search"},
            "agent_name": "atlas",
            "app_id": "todo",
            "summary": "searched docs",
        })
        assert resp.status_code == 200
        store.record.assert_awaited_once_with(
            event_type="agent_action",
            data={"action": "search"},
            agent_name="atlas",
            app_id="todo",
            summary="searched docs",
        )


# ---------------------------------------------------------------------------
# GET /api/archive/events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestQueryEvents:
    async def test_query_returns_events_list(self, client):
        store = _inject_archive(client._transport.app)
        store.query.return_value = [
            {"id": 1, "event_type": "user_message"},
            {"id": 2, "event_type": "agent_action"},
        ]
        resp = await client.get("/api/archive/events")
        assert resp.status_code == 200
        body = resp.json()
        assert "events" in body
        assert body["count"] == 2

    async def test_query_with_filters(self, client):
        store = _inject_archive(client._transport.app)
        store.query.return_value = []
        resp = await client.get("/api/archive/events", params={
            "event_type": "user_message",
            "agent_name": "atlas",
            "app_id": "todo",
            "since": 1000.0,
            "until": 2000.0,
            "search": "hello",
            "limit": 10,
            "offset": 5,
        })
        assert resp.status_code == 200
        store.query.assert_awaited_once_with(
            event_type="user_message",
            agent_name="atlas",
            app_id="todo",
            since=1000.0,
            until=2000.0,
            search="hello",
            limit=10,
            offset=5,
        )

    async def test_query_empty_results(self, client):
        _inject_archive(client._transport.app)
        resp = await client.get("/api/archive/events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["events"] == []
        assert body["count"] == 0


# ---------------------------------------------------------------------------
# GET /api/archive/events/{event_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetEvent:
    async def test_get_event_found(self, client):
        store = _inject_archive(client._transport.app)
        store.get_event.return_value = {"id": 1, "event_type": "user_message"}
        resp = await client.get("/api/archive/events/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 1

    async def test_get_event_not_found(self, client):
        store = _inject_archive(client._transport.app)
        store.get_event.return_value = None
        resp = await client.get("/api/archive/events/999")
        assert resp.status_code == 404
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/archive/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestArchiveStats:
    async def test_stats_returns_data(self, client):
        store = _inject_archive(client._transport.app)
        store.stats.return_value = {"total": 150, "by_type": {"user_message": 100}}
        resp = await client.get("/api/archive/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert "total" in body


# ---------------------------------------------------------------------------
# GET /api/archive/daily
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDailySummary:
    async def test_daily_with_date(self, client):
        store = _inject_archive(client._transport.app)
        store.daily_summary.return_value = {"date": "2026-01-15", "events": 25}
        resp = await client.get("/api/archive/daily", params={"date": "2026-01-15"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-01-15"
        store.daily_summary.assert_awaited_once_with(date="2026-01-15")

    async def test_daily_without_date(self, client):
        store = _inject_archive(client._transport.app)
        store.daily_summary.return_value = {"date": None, "events": 0}
        resp = await client.get("/api/archive/daily")
        assert resp.status_code == 200
        store.daily_summary.assert_awaited_once_with(date=None)


# ---------------------------------------------------------------------------
# GET /api/archive/export/{date}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestExportDay:
    async def test_export_returns_events(self, client):
        store = _inject_archive(client._transport.app)
        store.export_day.return_value = [{"id": 1}, {"id": 2}]
        resp = await client.get("/api/archive/export/2026-01-15")
        assert resp.status_code == 200
        body = resp.json()
        assert body["date"] == "2026-01-15"
        assert body["count"] == 2
        assert len(body["events"]) == 2

    async def test_export_empty_day(self, client):
        store = _inject_archive(client._transport.app)
        store.export_day.return_value = []
        resp = await client.get("/api/archive/export/2026-01-01")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/archive/tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSetTracking:
    async def test_enable_tracking(self, client):
        _inject_archive(client._transport.app)
        resp = await client.post("/api/archive/tracking", json={"enabled": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_tracking_enabled"] is True

    async def test_disable_tracking(self, client):
        _inject_archive(client._transport.app)
        resp = await client.post("/api/archive/tracking", json={"enabled": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_tracking_enabled"] is False

    async def test_tracking_rejects_missing_field(self, client):
        _inject_archive(client._transport.app)
        resp = await client.post("/api/archive/tracking", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/archive/tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetTracking:
    async def test_get_tracking_disabled(self, client):
        store = _inject_archive(client._transport.app)
        store.user_tracking_enabled = False
        resp = await client.get("/api/archive/tracking")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_tracking_enabled"] is False

    async def test_get_tracking_enabled(self, client):
        store = _inject_archive(client._transport.app)
        store.user_tracking_enabled = True
        resp = await client.get("/api/archive/tracking")
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_tracking_enabled"] is True


# ---------------------------------------------------------------------------
# POST /api/archive/compress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCompressOld:
    async def test_compress_default_days(self, client):
        store = _inject_archive(client._transport.app)
        store.compress_old_files.return_value = 3
        resp = await client.post("/api/archive/compress")
        assert resp.status_code == 200
        body = resp.json()
        assert body["compressed"] == 3
        store.compress_old_files.assert_awaited_once_with(1)

    async def test_compress_custom_days(self, client):
        store = _inject_archive(client._transport.app)
        store.compress_old_files.return_value = 0
        resp = await client.post("/api/archive/compress", params={"days_old": 7})
        assert resp.status_code == 200
        store.compress_old_files.assert_awaited_once_with(7)
