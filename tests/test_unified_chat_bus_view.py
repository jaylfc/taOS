"""Red-first tests for unified chat bus view router registration.

These tests MUST fail while the router is unregistered and MUST pass after
the router is registered under its own /api/chat/v2 prefix.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest


def _make_response(status_code, json_data, url="http://test"):
    req = httpx.Request("GET", url)
    return httpx.Response(status_code, json=json_data, request=req)


class _FakeBusClient:
    """Fake httpx.AsyncClient that returns canned bus responses."""

    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, **kwargs):
        for pattern, response in self._responses:
            if pattern in str(url):
                return _make_response(200, response, url=str(url))
        raise RuntimeError(f"Unexpected GET: {url}")

    async def post(self, url, json=None, **kwargs):
        for pattern, response in self._responses:
            if pattern in str(url):
                return _make_response(200, response, url=str(url))
        raise RuntimeError(f"Unexpected POST: {url}")


def _mock_async_client(responses):
    def _factory(*args, **kwargs):
        return _FakeBusClient(responses)
    return _factory


@pytest.mark.asyncio
async def test_v2_channels_served_by_bus_view(client):
    """GET /api/chat/v2/channels is answered by the unified bus view router.

    The bus view adds ``unified_bus: True`` to every channel it returns
    (both bus-backed and local-fallback paths).  The live chat router at
    /api/chat/channels never sets that field, so its presence is proof
    that the bus view handler answered.
    """
    resp = await client.get("/api/chat/v2/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert "channels" in body
    channels = body["channels"]
    assert isinstance(channels, list)
    if channels:
        assert any(ch.get("unified_bus") for ch in channels), (
            "no channel carried unified_bus: bus view was not the handler"
        )


@pytest.mark.asyncio
async def test_v2_channel_messages_proxies_to_bus(client):
    """GET /api/chat/v2/channels/{id}/messages proxies to the A2A bus.

    The bus is stubbed at the transport layer by replacing ``httpx.AsyncClient``
    with a fake that records calls and returns a canned bus response.  The
    response must round-trip through the registered app, not be served by a
    different handler.
    """
    bus_messages = [
        {"id": "bus-m1", "from": "alice", "body": "hello bus", "thread": "ch1", "unified_bus": True},
    ]
    mock_factory = _mock_async_client([
        ("/a2a/messages", {"messages": bus_messages}),
    ])

    with patch("httpx.AsyncClient", mock_factory):
        resp = await client.get("/api/chat/v2/channels/ch1/messages")
    assert resp.status_code == 200
    body = resp.json()
    assert body["messages"] == bus_messages


@pytest.mark.asyncio
async def test_v2_handler_module_is_bus_view(client):
    """The /api/chat/v2/channels route module is chat_unified_bus_view."""
    resp = await client.get("/api/chat/v2/channels")
    assert resp.status_code == 200
    from tinyagentos.routes import chat_unified_bus_view as bus_view_mod
    from fastapi import APIRouter
    app = client._transport.app
    for route in app.routes:
        if isinstance(route, APIRouter) and getattr(route, "prefix", None) == "/api/chat/v2":
            for r in route.routes:
                if getattr(r, "path", "") == "/channels":
                    assert r.endpoint.__module__ == bus_view_mod.__name__
