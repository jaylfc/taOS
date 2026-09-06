"""Tests for /api/a2a/bus/* read endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_bus_client(json_payload: dict):
    """Return a mock httpx.AsyncClient whose async context manager yields a
    client that returns *json_payload* from .get().json()."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = json_payload

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    return mock_ctx


@pytest.mark.asyncio
class TestBusChannels:
    async def test_get_channels_returns_200_with_list(self, client):
        payload = {
            "channels": [
                {
                    "channel": "general",
                    "members": ["a", "b"],
                    "message_count": 10,
                    "created_ts": 1000,
                    "last_ts": 2000,
                },
                {
                    "channel": "dev",
                    "members": ["c"],
                    "message_count": 5,
                    "created_ts": 500,
                    "last_ts": 3000,
                },
            ],
        }
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=_mock_bus_client(payload),
        ):
            resp = await client.get("/api/a2a/bus/channels")
        assert resp.status_code == 200
        data = resp.json()
        assert "channels" in data
        assert isinstance(data["channels"], list)
        assert len(data["channels"]) == 2
        assert data["available"] is True

    async def test_get_channels_sorted_by_last_ts_newest_first(self, client):
        payload = {
            "channels": [
                {
                    "channel": "old",
                    "members": [],
                    "message_count": 0,
                    "created_ts": 100,
                    "last_ts": 500,
                },
                {
                    "channel": "new",
                    "members": [],
                    "message_count": 0,
                    "created_ts": 200,
                    "last_ts": 9000,
                },
                {
                    "channel": "mid",
                    "members": [],
                    "message_count": 0,
                    "created_ts": 150,
                    "last_ts": 2000,
                },
            ],
        }
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=_mock_bus_client(payload),
        ):
            resp = await client.get("/api/a2a/bus/channels")
        channels = resp.json()["channels"]
        assert channels[0]["channel"] == "new"
        assert channels[1]["channel"] == "mid"
        assert channels[2]["channel"] == "old"

    async def test_get_channels_degraded_when_bus_unavailable(self, client):
        mock_client = AsyncMock()
        mock_client.get.side_effect = ConnectionError("bus unreachable")

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=mock_ctx,
        ):
            resp = await client.get("/api/a2a/bus/channels")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"] == []
        assert data["available"] is False

    async def test_get_channels_empty_list(self, client):
        payload = {"channels": []}
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=_mock_bus_client(payload),
        ):
            resp = await client.get("/api/a2a/bus/channels")
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"] == []
        assert data["available"] is True


@pytest.mark.asyncio
class TestBusMessages:
    async def test_get_messages_returns_200_with_list(self, client):
        payload = {
            "messages": [
                {
                    "id": "msg-1",
                    "ts": 1000,
                    "from": "agent-a",
                    "body": "hello",
                    "thread": "general",
                    "reply_to": None,
                },
                {
                    "id": "msg-2",
                    "ts": 2000,
                    "from": "agent-b",
                    "body": "hi there",
                    "thread": "general",
                    "reply_to": "msg-1",
                },
            ],
        }
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=_mock_bus_client(payload),
        ):
            resp = await client.get(
                "/api/a2a/bus/messages",
                params={"channel": "general"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) == 2
        assert data["available"] is True

    async def test_get_messages_empty_list(self, client):
        payload = {"messages": []}
        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=_mock_bus_client(payload),
        ):
            resp = await client.get(
                "/api/a2a/bus/messages",
                params={"channel": "general"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["available"] is True

    async def test_get_messages_requires_channel(self, client):
        resp = await client.get("/api/a2a/bus/messages")
        assert resp.status_code == 400
        data = resp.json()
        assert "error" in data

    async def test_get_messages_degraded_when_bus_unavailable(self, client):
        mock_client = AsyncMock()
        mock_client.get.side_effect = ConnectionError("bus unreachable")

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "tinyagentos.routes.a2a_bus.httpx.AsyncClient",
            return_value=mock_ctx,
        ):
            resp = await client.get(
                "/api/a2a/bus/messages",
                params={"channel": "general"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["messages"] == []
        assert data["available"] is False
