"""Tests to verify ChatBusBridge removal.

These tests verify that the inert ChatBusBridge has been removed:
1. The module cannot be imported
2. The app.state.chat_bus_bridge attribute does not exist
3. The read-only bus view routes still work
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_unified_chat_bridge_module_removed():
    """Verify that the unified_chat_bridge module no longer exists."""
    with pytest.raises(ModuleNotFoundError):
        import tinyagentos.chat.unified_chat_bridge


@pytest.mark.asyncio
async def test_unified_chat_bridge_not_in_app_state(app, client):
    """Verify that the app.state.chat_bus_bridge attribute no longer exists."""
    # This tests the app fixture from routes/conftest.py
    assert not hasattr(app.state, "chat_bus_bridge"), (
        "app.state.chat_bus_bridge should not exist after removal"
    )


@pytest.mark.asyncio
async def test_api_chat_v2_channels_still_works(client):
    """Verify that the read-only bus view routes still work after removal."""
    # Reusing the assertion pattern from tests/test_unified_chat_bus_view.py
    bus_channels = [
        {"id": "ch1", "name": "bus channel"},
    ]
    from unittest.mock import patch
    
    # Mock the httpx.AsyncClient to simulate bus responses
    class _FakeBusClient:
        def __init__(self, responses):
            self._responses = responses

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, **kwargs):
            for pattern, response in self._responses:
                if pattern in str(url):
                    import httpx
                    req = httpx.Request("GET", url)
                    return httpx.Response(200, json=response, request=req)
            raise RuntimeError(f"Unexpected GET: {url}")

        async def post(self, url, json=None, **kwargs):
            for pattern, response in self._responses:
                if pattern in str(url):
                    import httpx
                    req = httpx.Request("POST", url)
                    return httpx.Response(200, json=response, request=req)
            raise RuntimeError(f"Unexpected POST: {url}")

    def _mock_async_client(responses):
        def _factory(*args, **kwargs):
            return _FakeBusClient(responses)
        return _factory

    mock_factory = _mock_async_client([
        ("/a2a/channels", {"channels": bus_channels}),
    ])

    with patch("httpx.AsyncClient", mock_factory):
        resp = await client.get("/api/chat/v2/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert "channels" in body
    channels = body["channels"]
    assert isinstance(channels, list)
    assert len(channels) >= 1
    assert any(ch.get("unified_bus") for ch in channels), (
        "no channel carried unified_bus: bus view was not the handler"
    )
