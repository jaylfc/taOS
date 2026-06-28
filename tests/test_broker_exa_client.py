"""Tests for the Exa search client in tinyagentos.broker.clients.exa."""

from __future__ import annotations

import json

import pytest

import respx
from httpx import Response

from tinyagentos.broker.clients.exa import _BASE_URL, exa_search


@pytest.mark.asyncio
@respx.mock
async def test_exa_search_returns_results():
    route = respx.post(f"{_BASE_URL}/search").mock(
        return_value=Response(200, json={"results": [{"title": "t", "url": "u"}]})
    )
    result = await exa_search("test-key", "hello", num_results=3)
    assert result == [{"title": "t", "url": "u"}]

    call = route.calls[0]
    assert call.request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(call.request.content)
    assert body["query"] == "hello"
    assert body["numResults"] == 3


@pytest.mark.asyncio
@respx.mock
async def test_exa_search_non_200_raises():
    respx.post(f"{_BASE_URL}/search").mock(
        return_value=Response(500, text="internal error")
    )
    with pytest.raises(RuntimeError, match="500"):
        await exa_search("key", "q")
