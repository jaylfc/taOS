from __future__ import annotations

"""Route-level tests for /api/x/* endpoints.

Uses a minimal FastAPI app that includes only the x router, so we
do NOT need to modify tinyagentos/app.py for these tests.
"""

import pytest
import pytest_asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tinyagentos.routes.x import router as x_router
from tinyagentos.knowledge_fetchers.x import XWatchStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TWEET = {
    "id": "1234567890",
    "author": "Test User",
    "handle": "testhandle",
    "text": "Hello from the tests!",
    "likes": 10,
    "reposts": 2,
    "views": 500,
    "created_at": 1700000000.0,
    "media": [],
}


def _build_test_app(tmp_path: Path) -> FastAPI:
    """Build a minimal FastAPI app with x router and isolated watch store."""
    app = FastAPI()
    app.include_router(x_router)
    app.state.http_client = AsyncMock()

    store = XWatchStore(db_path=tmp_path / "x-watches.db")
    store.init()
    app.state.x_watch_store = store

    return app


@pytest_asyncio.fixture
async def client(tmp_path):
    app = _build_test_app(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# GET /api/x/auth/status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_status_unauthenticated(client):
    resp = await client.get("/api/x/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False


# ---------------------------------------------------------------------------
# GET /api/x/tweet/{tweet_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_tweet_success(client):
    with patch(
        "tinyagentos.routes.x.fetch_tweet_ytdlp",
        new=AsyncMock(return_value=SAMPLE_TWEET),
    ):
        resp = await client.get("/api/x/tweet/1234567890")

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "1234567890"
    assert data["handle"] == "testhandle"
    assert data["text"] == "Hello from the tests!"
    assert "metadata" in data
    assert data["metadata"]["likes"] == 10


@pytest.mark.asyncio
async def test_get_tweet_not_found(client):
    with patch(
        "tinyagentos.routes.x.fetch_tweet_ytdlp",
        new=AsyncMock(return_value=None),
    ):
        resp = await client.get("/api/x/tweet/9999999")

    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_get_tweet_backend_error(client):
    with patch(
        "tinyagentos.routes.x.fetch_tweet_ytdlp",
        new=AsyncMock(side_effect=RuntimeError("yt-dlp crashed")),
    ):
        resp = await client.get("/api/x/tweet/badtweet")

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /api/x/thread/{tweet_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_thread_single_tweet(client):
    with patch(
        "tinyagentos.routes.x.reconstruct_thread",
        new=AsyncMock(return_value=[SAMPLE_TWEET]),
    ):
        resp = await client.get("/api/x/thread/1234567890")

    assert resp.status_code == 200
    data = resp.json()
    assert "tweets" in data
    assert len(data["tweets"]) == 1
    assert "text" in data
    assert "@testhandle" in data["text"]


@pytest.mark.asyncio
async def test_get_thread_empty(client):
    with patch(
        "tinyagentos.routes.x.reconstruct_thread",
        new=AsyncMock(return_value=[]),
    ):
        resp = await client.get("/api/x/thread/9999999")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tweets"] == []
    assert data["text"] == ""


# ---------------------------------------------------------------------------
# Watch CRUD endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_watch(client):
    resp = await client.post(
        "/api/x/watch",
        json={"handle": "elonmusk", "frequency": 3600},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["handle"] == "elonmusk"
    assert data["frequency"] == 3600
    assert data["enabled"] == 1


@pytest.mark.asyncio
async def test_create_watch_with_at_prefix(client):
    resp = await client.post(
        "/api/x/watch",
        json={"handle": "@someuser", "frequency": 900},
    )
    assert resp.status_code == 200
    assert resp.json()["handle"] == "someuser"


@pytest.mark.asyncio
async def test_create_watch_with_filters(client):
    resp = await client.post(
        "/api/x/watch",
        json={
            "handle": "techuser",
            "filters": {"min_likes": 100, "threads_only": True},
            "frequency": 1800,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["filters"]["min_likes"] == 100
    assert data["filters"]["threads_only"] is True


@pytest.mark.asyncio
async def test_create_watch_duplicate_returns_409(client):
    await client.post("/api/x/watch", json={"handle": "dupuser"})
    resp = await client.post("/api/x/watch", json={"handle": "dupuser"})
    assert resp.status_code == 409
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_list_watches_empty(client):
    resp = await client.get("/api/x/watches")
    assert resp.status_code == 200
    data = resp.json()
    assert data["watches"] == []


@pytest.mark.asyncio
async def test_list_watches_after_create(client):
    await client.post("/api/x/watch", json={"handle": "alice"})
    await client.post("/api/x/watch", json={"handle": "bob"})

    resp = await client.get("/api/x/watches")
    assert resp.status_code == 200
    watches = resp.json()["watches"]
    assert len(watches) == 2
    handles = {w["handle"] for w in watches}
    assert "alice" in handles
    assert "bob" in handles


@pytest.mark.asyncio
async def test_update_watch_frequency(client):
    await client.post("/api/x/watch", json={"handle": "charlie", "frequency": 1800})
    resp = await client.put("/api/x/watch/charlie", json={"frequency": 600})
    assert resp.status_code == 200
    assert resp.json()["frequency"] == 600


@pytest.mark.asyncio
async def test_update_watch_enabled(client):
    await client.post("/api/x/watch", json={"handle": "dave"})
    resp = await client.put("/api/x/watch/dave", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] == 0


@pytest.mark.asyncio
async def test_update_watch_not_found(client):
    resp = await client.put("/api/x/watch/ghost", json={"frequency": 300})
    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_delete_watch(client):
    await client.post("/api/x/watch", json={"handle": "frank"})
    resp = await client.delete("/api/x/watch/frank")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Confirm it's gone
    list_resp = await client.get("/api/x/watches")
    handles = {w["handle"] for w in list_resp.json()["watches"]}
    assert "frank" not in handles


@pytest.mark.asyncio
async def test_delete_watch_not_found(client):
    resp = await client.delete("/api/x/watch/nobody")
    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_delete_watch_at_prefix(client):
    await client.post("/api/x/watch", json={"handle": "grace"})
    resp = await client.delete("/api/x/watch/@grace")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True
