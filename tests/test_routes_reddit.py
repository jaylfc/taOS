from __future__ import annotations

"""Route-level tests for /api/reddit/* endpoints.

Uses a minimal FastAPI app that includes only the reddit router, so we
do NOT need to modify tinyagentos/app.py for these tests.
"""

import json
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tinyagentos.routes.reddit import router as reddit_router

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str):
    return json.loads((FIXTURES_DIR / name).read_text())


def _make_mock_http_response(json_data) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_data)
    return resp


def _make_subreddit_listing(posts_data: list[dict], after: str | None = None) -> dict:
    children = [{"kind": "t3", "data": pd} for pd in posts_data]
    return {
        "kind": "Listing",
        "data": {"children": children, "after": after, "before": None},
    }


def _post_data_stub(idx: int) -> dict:
    return {
        "id": f"post{idx:03d}",
        "subreddit": "LocalLLaMA",
        "title": f"Post number {idx}",
        "author": f"author{idx}",
        "selftext": "",
        "score": idx * 10,
        "upvote_ratio": 0.9,
        "num_comments": idx,
        "created_utc": float(1712000000 + idx),
        "url": f"https://reddit.com/r/LocalLLaMA/comments/post{idx:03d}/",
        "permalink": f"/r/LocalLLaMA/comments/post{idx:03d}/",
        "link_flair_text": None,
        "is_self": True,
    }


class _FakeSecretsStore:
    """Minimal in-memory secrets store for testing."""

    def __init__(self):
        self._data: dict[str, str] = {}

    async def get(self, name: str) -> dict | None:
        if name in self._data:
            return {"value": self._data[name]}
        return None

    async def add(self, name: str, value: str, **kwargs) -> int:
        self._data[name] = value
        return 1


def _build_test_app(secrets: _FakeSecretsStore | None = None) -> FastAPI:
    """Build a minimal FastAPI app with reddit router and mock state."""
    app = FastAPI()
    app.include_router(reddit_router)
    app.state.http_client = AsyncMock()
    app.state.secrets = secrets or _FakeSecretsStore()
    return app


@pytest_asyncio.fixture
async def reddit_client():
    """Async HTTP test client with reddit router only."""
    app = _build_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app  # expose app for state manipulation in tests
        yield c


# ---------------------------------------------------------------------------
# GET /api/reddit/thread
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_thread_returns_post_and_comments(reddit_client):
    fixture = _load_fixture("reddit_thread.json")
    mock_resp = _make_mock_http_response(fixture)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/thread",
        params={"url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "post" in data
    assert "comments" in data
    assert "text" in data
    assert "metadata" in data
    assert data["post"]["id"] == "abc123"
    assert data["post"]["subreddit"] == "LocalLLaMA"


@pytest.mark.asyncio
async def test_get_thread_missing_url(reddit_client):
    # FastAPI returns 422 for missing required query param
    resp = await reddit_client.get("/api/reddit/thread")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_thread_upstream_error(reddit_client):
    reddit_client.app.state.http_client.get = AsyncMock(
        side_effect=Exception("connection refused")
    )
    resp = await reddit_client.get(
        "/api/reddit/thread",
        params={"url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"},
    )
    assert resp.status_code == 502
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_get_thread_text_contains_title(reddit_client):
    fixture = _load_fixture("reddit_thread.json")
    mock_resp = _make_mock_http_response(fixture)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/thread",
        params={"url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"},
    )
    assert resp.status_code == 200
    text = resp.json()["text"]
    assert "Running LLMs on Orange Pi 5" in text
    assert "---" in text


@pytest.mark.asyncio
async def test_get_thread_metadata_keys(reddit_client):
    fixture = _load_fixture("reddit_thread.json")
    mock_resp = _make_mock_http_response(fixture)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/thread",
        params={"url": "https://www.reddit.com/r/LocalLLaMA/comments/abc123/"},
    )
    meta = resp.json()["metadata"]
    for key in ("subreddit", "score", "upvote_ratio", "num_comments", "created_utc", "flair", "is_self"):
        assert key in meta, f"missing metadata key: {key}"


# ---------------------------------------------------------------------------
# GET /api/reddit/subreddit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_subreddit_returns_posts(reddit_client):
    stubs = [_post_data_stub(i) for i in range(3)]
    listing = _make_subreddit_listing(stubs, after="t3_post002")
    mock_resp = _make_mock_http_response(listing)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/subreddit",
        params={"name": "LocalLLaMA", "sort": "hot"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["subreddit"] == "LocalLLaMA"
    assert data["sort"] == "hot"
    assert len(data["posts"]) == 3
    assert data["after"] == "t3_post002"


@pytest.mark.asyncio
async def test_get_subreddit_missing_name(reddit_client):
    resp = await reddit_client.get("/api/reddit/subreddit")
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_get_subreddit_upstream_error(reddit_client):
    reddit_client.app.state.http_client.get = AsyncMock(
        side_effect=Exception("timeout")
    )
    resp = await reddit_client.get(
        "/api/reddit/subreddit",
        params={"name": "LocalLLaMA"},
    )
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_get_subreddit_empty_listing(reddit_client):
    listing = _make_subreddit_listing([], after=None)
    mock_resp = _make_mock_http_response(listing)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/subreddit",
        params={"name": "emptysub"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["posts"] == []
    assert data["after"] is None


# ---------------------------------------------------------------------------
# GET /api/reddit/search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_results(reddit_client):
    stubs = [_post_data_stub(i) for i in range(2)]
    listing = _make_subreddit_listing(stubs)
    mock_resp = _make_mock_http_response(listing)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/search",
        params={"q": "rkllama npu"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "rkllama npu"
    assert "posts" in data
    assert len(data["posts"]) == 2


@pytest.mark.asyncio
async def test_search_missing_q(reddit_client):
    resp = await reddit_client.get("/api/reddit/search")
    assert resp.status_code == 400
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_search_subreddit_restriction(reddit_client):
    stubs = [_post_data_stub(0)]
    listing = _make_subreddit_listing(stubs)
    mock_resp = _make_mock_http_response(listing)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get(
        "/api/reddit/search",
        params={"q": "npu", "subreddit": "LocalLLaMA"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["subreddit"] == "LocalLLaMA"
    # Verify the URL sent included the subreddit path
    call_url = reddit_client.app.state.http_client.get.call_args[0][0]
    assert "LocalLLaMA" in call_url


# ---------------------------------------------------------------------------
# GET /api/reddit/saved
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saved_no_token_returns_401(reddit_client):
    # Default _FakeSecretsStore has no token
    resp = await reddit_client.get("/api/reddit/saved")
    assert resp.status_code == 401
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_saved_with_token_returns_posts(reddit_client):
    # Store a token in the fake secrets store
    await reddit_client.app.state.secrets.add("REDDIT_TOKEN", "fake_bearer_token")

    stubs = [_post_data_stub(i) for i in range(2)]
    listing = _make_subreddit_listing(stubs)
    mock_resp = _make_mock_http_response(listing)
    reddit_client.app.state.http_client.get = AsyncMock(return_value=mock_resp)

    resp = await reddit_client.get("/api/reddit/saved")
    assert resp.status_code == 200
    data = resp.json()
    assert "posts" in data
    assert len(data["posts"]) == 2

    # Verify Authorization header was sent
    call_kwargs = reddit_client.app.state.http_client.get.call_args[1]
    headers = call_kwargs.get("headers", {})
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer fake_bearer_token"


# ---------------------------------------------------------------------------
# GET /api/reddit/auth/status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_status_no_token(reddit_client):
    resp = await reddit_client.get("/api/reddit/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["username"] is None


@pytest.mark.asyncio
async def test_auth_status_with_valid_token(reddit_client):
    await reddit_client.app.state.secrets.add("REDDIT_TOKEN", "valid_token")

    me_response = MagicMock()
    me_response.raise_for_status = MagicMock()
    me_response.json = MagicMock(return_value={"name": "edge_hacker", "id": "abc"})
    reddit_client.app.state.http_client.get = AsyncMock(return_value=me_response)

    resp = await reddit_client.get("/api/reddit/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is True
    assert data["username"] == "edge_hacker"


@pytest.mark.asyncio
async def test_auth_status_with_invalid_token(reddit_client):
    await reddit_client.app.state.secrets.add("REDDIT_TOKEN", "expired_token")

    reddit_client.app.state.http_client.get = AsyncMock(
        side_effect=Exception("401 Unauthorized")
    )

    resp = await reddit_client.get("/api/reddit/auth/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["authenticated"] is False
    assert data["username"] is None
