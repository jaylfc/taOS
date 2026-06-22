"""Tests for the Tavily search client (respx-mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from tinyagentos.broker.clients.tavily import TAVILY_SEARCH_URL, tavily_search


@pytest.mark.asyncio
class TestTavilySearch:
    @respx.mock
    async def test_success_returns_results_list(self):
        route = respx.post(TAVILY_SEARCH_URL).mock(
            return_value=Response(200, json={"results": [{"title": "t", "url": "https://example.com"}]})
        )
        results = await tavily_search("test-key", "hello", num_results=3)
        assert results == [{"title": "t", "url": "https://example.com"}]
        assert route.called
        import json as _json
        body = _json.loads(route.calls[0].request.content)
        assert body["api_key"] == "test-key"
        assert body["query"] == "hello"
        assert body["max_results"] == 3

    @respx.mock
    async def test_non_200_raises_runtime_error_with_status(self):
        respx.post(TAVILY_SEARCH_URL).mock(
            return_value=Response(500, text="internal error")
        )
        with pytest.raises(RuntimeError, match="500"):
            await tavily_search("key", "q")

    @respx.mock
    async def test_default_num_results_is_5(self):
        route = respx.post(TAVILY_SEARCH_URL).mock(
            return_value=Response(200, json={"results": []})
        )
        await tavily_search("key", "q")
        import json as _json
        body = _json.loads(route.calls[0].request.content)
        assert body["max_results"] == 5

    @respx.mock
    async def test_no_authorization_header(self):
        route = respx.post(TAVILY_SEARCH_URL).mock(
            return_value=Response(200, json={"results": []})
        )
        await tavily_search("secret-key", "q")
        headers = route.calls[0].request.headers
        assert "authorization" not in headers
