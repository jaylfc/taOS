from __future__ import annotations

import respx
import httpx
import pytest


MOCK_SEARCH_RESPONSE = {
    "data": [
        {
            "id": "abc123",
            "url": "https://wallhaven.cc/w/abc123",
            "path": "https://w.wallhaven.cc/full/abc/wallhaven-abc123.jpg",
            "thumbs": {
                "small": "https://th.wallhaven.cc/small/ab/abc123.jpg",
                "original": "https://th.wallhaven.cc/original/ab/abc123.jpg",
                "large": "https://th.wallhaven.cc/large/ab/abc123.jpg",
            },
            "resolution": "1920x1080",
            "category": "general",
            "purity": "sfw",
        },
        {
            "id": "def456",
            "url": "https://wallhaven.cc/w/def456",
            "path": "https://w.wallhaven.cc/full/de/wallhaven-def456.jpg",
            "thumbs": {
                "small": "https://th.wallhaven.cc/small/de/def456.jpg",
                "original": "https://th.wallhaven.cc/original/de/def456.jpg",
                "large": "https://th.wallhaven.cc/large/de/def456.jpg",
            },
            "resolution": "2560x1440",
            "category": "anime",
            "purity": "sfw",
        },
    ],
    "meta": {
        "current_page": 1,
        "last_page": 5,
        "total": 50,
    },
}

MOCK_EMPTY_RESPONSE = {
    "data": [],
    "meta": {"current_page": 1, "last_page": 1, "total": 0},
}


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_results(client):
    """Search with a query should return Wallhaven results."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    resp = await client.get("/api/wallhaven/search?q=nature")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 2
    assert data["data"][0]["id"] == "abc123"
    assert data["meta"]["current_page"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_empty_results(client):
    """Empty search results should return empty data array."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_EMPTY_RESPONSE),
    )

    resp = await client.get("/api/wallhaven/search?q=nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"] == []
    assert data["meta"]["total"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_search_default_params(client):
    """Default pagination (page 1) and default category/purity should be sent."""
    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search?q=nature")
    assert route.called
    # Verify params were forwarded: page=1, categories=111, purity=100
    req = route.calls.last.request
    assert req.url.params["page"] == "1"
    assert req.url.params["categories"] == "111"
    assert req.url.params["purity"] == "100"


@pytest.mark.asyncio
@respx.mock
async def test_search_pagination(client):
    """Page parameter should be forwarded."""
    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search?q=nature&page=3")
    assert route.calls.last.request.url.params["page"] == "3"


@pytest.mark.asyncio
@respx.mock
async def test_search_custom_categories_purity(client):
    """Custom categories and purity should be forwarded."""
    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search?q=anime&categories=010&purity=110")
    req = route.calls.last.request
    assert req.url.params["categories"] == "010"
    assert req.url.params["purity"] == "110"


@pytest.mark.asyncio
@respx.mock
async def test_rate_limited(client):
    """429 from Wallhaven should return 429 with friendly message."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(429),
    )

    resp = await client.get("/api/wallhaven/search?q=nature")
    assert resp.status_code == 429
    data = resp.json()
    assert "rate limited" in data["error"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_wallhaven_down(client):
    """502 from Wallhaven should return 502."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(502),
    )

    resp = await client.get("/api/wallhaven/search?q=nature")
    assert resp.status_code == 502


@pytest.mark.asyncio
@respx.mock
async def test_wallhaven_500(client):
    """500 from Wallhaven should return 502 proxy error."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(500),
    )

    resp = await client.get("/api/wallhaven/search?q=nature")
    assert resp.status_code == 502


@pytest.mark.asyncio
@respx.mock
async def test_timeout(client):
    """Timeout should return 504."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        side_effect=httpx.TimeoutException("timed out"),
    )

    resp = await client.get("/api/wallhaven/search?q=nature")
    assert resp.status_code == 504
    data = resp.json()
    assert "timed out" in data["error"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_request_error(client):
    """Connection error should return 502."""
    respx.get("https://wallhaven.cc/api/v1/search").mock(
        side_effect=httpx.RequestError("connection refused"),
    )

    resp = await client.get("/api/wallhaven/search?q=nature")
    assert resp.status_code == 502


@pytest.mark.asyncio
@respx.mock
async def test_api_key_sent_when_configured(app, client):
    """When wallhaven_api_key is set, X-API-Key header should be sent."""
    app.state.config.wallhaven_api_key = "test-key-123"

    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search?q=nature")
    assert route.calls.last.request.headers["X-API-Key"] == "test-key-123"


@pytest.mark.asyncio
@respx.mock
async def test_no_api_key_when_not_configured(client):
    """When wallhaven_api_key is None, no X-API-Key header should be sent."""
    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search?q=nature")
    assert "X-API-Key" not in route.calls.last.request.headers


@pytest.mark.asyncio
@respx.mock
async def test_invalid_sorting_rejected(client):
    """Non-whitelisted sorting value should return 400."""
    resp = await client.get("/api/wallhaven/search?q=nature&sorting=invalid_sort")
    assert resp.status_code == 400
    data = resp.json()
    assert "sorting" in data["error"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_valid_sorting_forwarded(client):
    """Known sorting value should be forwarded to Wallhaven."""
    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search?q=nature&sorting=toplist")
    assert route.calls.last.request.url.params["sorting"] == "toplist"


@pytest.mark.asyncio
@respx.mock
async def test_query_max_length_enforced(client):
    """q param longer than 200 chars should be rejected."""
    long_q = "x" * 201
    resp = await client.get(f"/api/wallhaven/search?q={long_q}")
    assert resp.status_code == 422  # FastAPI validation error


@pytest.mark.asyncio
@respx.mock
async def test_empty_query_allowed(client):
    """Empty q (default) should pass through to Wallhaven for unfiltered feed."""
    route = respx.get("https://wallhaven.cc/api/v1/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE),
    )

    await client.get("/api/wallhaven/search")
    assert route.calls.last.request.url.params["q"] == ""
