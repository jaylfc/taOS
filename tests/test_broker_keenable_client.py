"""Tests for the Keenable search/fetch client (respx-mocked)."""

from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from tinyagentos.broker.clients.keenable import (
    KEENABLE_APP_TITLE,
    KEENABLE_FETCH_URL,
    KEENABLE_SEARCH_URL,
    keenable_fetch,
    keenable_search,
)
from tinyagentos.broker.providers import get_provider


def test_provider_registered_with_header_auth():
    p = get_provider("keenable")
    assert p is not None
    assert p.kind == "mcp"
    assert p.auth_style == "header:X-Api-Key"
    assert p.secret_ref == "KEENABLE_API_KEY"
    assert p.default_scopes == ["search", "fetch"]


@pytest.mark.asyncio
class TestKeenableClient:
    @respx.mock
    async def test_search_with_key_uses_keyed_endpoint_and_header(self):
        route = respx.post(KEENABLE_SEARCH_URL).mock(
            return_value=Response(200, json={"results": [{"title": "t", "url": "https://example.com"}]})
        )
        results = await keenable_search("secret-key", "hello", num_results=3)
        assert results == [{"title": "t", "url": "https://example.com"}]
        req = route.calls[0].request
        assert req.headers["X-API-Key"] == "secret-key"
        assert "X-Keenable-Title" not in req.headers
        assert json.loads(req.content) == {"query": "hello", "max_results": 3}
        assert "secret-key" not in str(req.url)

    @respx.mock
    async def test_search_without_key_uses_public_endpoint(self):
        route = respx.post(f"{KEENABLE_SEARCH_URL}/public").mock(
            return_value=Response(200, json={"results": []})
        )
        assert await keenable_search("", "hello", allow_public=True) == []
        req = route.calls[0].request
        assert req.headers["X-Keenable-Title"] == KEENABLE_APP_TITLE
        assert "X-API-Key" not in req.headers

    @respx.mock
    async def test_search_non_200_raises_with_status(self):
        respx.post(KEENABLE_SEARCH_URL).mock(return_value=Response(429, text="slow down"))
        with pytest.raises(RuntimeError, match="429"):
            await keenable_search("key", "q")

    @respx.mock
    async def test_fetch_passes_url_and_max_chars(self):
        route = respx.get(KEENABLE_FETCH_URL).mock(
            return_value=Response(200, json={"url": "https://example.com/", "title": "Example", "content": "# Hi"})
        )
        page = await keenable_fetch("key", "https://example.com", max_chars=500)
        assert page["content"] == "# Hi"
        req = route.calls[0].request
        assert req.url.params["url"] == "https://example.com"
        assert req.url.params["max_chars"] == "500"
        assert req.headers["X-API-Key"] == "key"

    @respx.mock
    async def test_fetch_non_200_raises(self):
        respx.get(f"{KEENABLE_FETCH_URL}/public").mock(return_value=Response(500, text="boom"))
        with pytest.raises(RuntimeError, match="500"):
            await keenable_fetch("", "https://example.com", allow_public=True)

    @respx.mock
    async def test_empty_key_default_raises(self):
        """Empty key with default allow_public=False should raise without HTTP call."""
        with pytest.raises(RuntimeError, match="Keenable API key not set and public fallback is off"):
            await keenable_search("", "hello")
        # No HTTP call should have been made
        assert len(respx.calls) == 0

    @respx.mock
    async def test_empty_key_allow_public_true_uses_public_endpoint(self):
        """Empty key with allow_public=True should use /public endpoint."""
        route = respx.post(f"{KEENABLE_SEARCH_URL}/public").mock(
            return_value=Response(200, json={"results": [{"title": "t", "url": "https://example.com"}]})
        )
        results = await keenable_search("", "hello", allow_public=True)
        assert results == [{"title": "t", "url": "https://example.com"}]
        req = route.calls[0].request
        assert req.headers["X-Keenable-Title"] == KEENABLE_APP_TITLE
        assert "X-API-Key" not in req.headers

    @respx.mock
    async def test_non_empty_key_ignores_allow_public(self):
        """Non-empty key should use keyed endpoint regardless of allow_public."""
        route = respx.post(KEENABLE_SEARCH_URL).mock(
            return_value=Response(200, json={"results": [{"title": "t", "url": "https://example.com"}]})
        )
        results = await keenable_search("secret-key", "hello", allow_public=True)
        assert results == [{"title": "t", "url": "https://example.com"}]
        req = route.calls[0].request
        assert req.headers["X-API-Key"] == "secret-key"
        assert "X-Keenable-Title" not in req.headers

    @respx.mock
    async def test_fetch_empty_key_default_raises(self):
        """Empty key with default allow_public=False should raise without HTTP call for fetch."""
        with pytest.raises(RuntimeError, match="Keenable API key not set and public fallback is off"):
            await keenable_fetch("", "https://example.com")
        assert len(respx.calls) == 0

    @respx.mock
    async def test_fetch_empty_key_allow_public_true_uses_public_endpoint(self):
        """Empty key with allow_public=True should use /public endpoint for fetch."""
        route = respx.get(f"{KEENABLE_FETCH_URL}/public").mock(
            return_value=Response(200, json={"url": "https://example.com/", "title": "Example", "content": "# Hi"})
        )
        page = await keenable_fetch("", "https://example.com", allow_public=True)
        assert page["content"] == "# Hi"
        req = route.calls[0].request
        assert req.headers["X-Keenable-Title"] == KEENABLE_APP_TITLE
        assert "X-API-Key" not in req.headers

    async def test_public_fallback_enabled_false_when_missing(self):
        """public_fallback_enabled returns False when preference is missing."""
        from tinyagentos.broker.clients.keenable import public_fallback_enabled

        class MockSettings:
            async def get_preference(self, user_id, namespace):
                return {}

        assert await public_fallback_enabled(MockSettings()) is False

    async def test_public_fallback_enabled_false_when_not_dict(self):
        """public_fallback_enabled returns False when preference is not a dict."""
        from tinyagentos.broker.clients.keenable import public_fallback_enabled

        class MockSettings:
            async def get_preference(self, user_id, namespace):
                return "not a dict"

        assert await public_fallback_enabled(MockSettings()) is False

    async def test_public_fallback_enabled_false_when_key_missing(self):
        """public_fallback_enabled returns False when key is missing from dict."""
        from tinyagentos.broker.clients.keenable import public_fallback_enabled

        class MockSettings:
            async def get_preference(self, user_id, namespace):
                return {"other_key": True}

        assert await public_fallback_enabled(MockSettings()) is False

    async def test_public_fallback_enabled_true_when_set(self):
        """public_fallback_enabled returns True when keenable_public_fallback is True."""
        from tinyagentos.broker.clients.keenable import public_fallback_enabled

        class MockSettings:
            async def get_preference(self, user_id, namespace):
                return {"keenable_public_fallback": True}

        assert await public_fallback_enabled(MockSettings()) is True
