"""Route tests for the client-log capture API."""
import pytest

from tinyagentos.auth_context import CurrentUser, current_user


@pytest.mark.asyncio
async def test_post_client_log_returns_201(client):
    resp = await client.post(
        "/api/client-logs",
        json={"level": "error", "message": "boom", "source": "MessagesApp",
              "url": "/desktop", "stack": "at render"},
    )
    assert resp.status_code == 201
    d = resp.json()
    assert d["level"] == "error"
    assert d["message"] == "boom"
    assert "id" in d and "created_at" in d


@pytest.mark.asyncio
async def test_get_lists_posted_log(client):
    await client.post("/api/client-logs", json={"level": "warn", "message": "hmm"})
    resp = await client.get("/api/client-logs")
    assert resp.status_code == 200
    msgs = [i["message"] for i in resp.json()["items"]]
    assert "hmm" in msgs


@pytest.mark.asyncio
async def test_get_filters_by_level(client):
    await client.post("/api/client-logs", json={"level": "error", "message": "E"})
    await client.post("/api/client-logs", json={"level": "info", "message": "I"})
    resp = await client.get("/api/client-logs?level=error")
    items = resp.json()["items"]
    assert items and all(i["level"] == "error" for i in items)
    assert "E" in [i["message"] for i in items]


@pytest.mark.asyncio
async def test_invalid_level_rejected(client):
    resp = await client.post("/api/client-logs", json={"level": "bogus", "message": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_empty_message_rejected(client):
    resp = await client.post("/api/client-logs", json={"level": "error", "message": "   "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_is_rate_limited_per_user(client, monkeypatch):
    from tinyagentos.rate_limit import RateLimiter

    # Small, non-refilling bucket so a flood is deterministically capped: the
    # 4th valid post past a capacity of 3 is rejected, protecting other users'
    # entries in the shared ring buffer.
    monkeypatch.setattr(
        "tinyagentos.routes.client_logs._post_limiter",
        RateLimiter(capacity=3, refill_per_second=0.0),
    )
    for _ in range(3):
        ok = await client.post("/api/client-logs", json={"level": "error", "message": "flood"})
        assert ok.status_code == 201
    blocked = await client.post("/api/client-logs", json={"level": "error", "message": "flood"})
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_non_admin_can_post_but_not_read(app, client):
    app.dependency_overrides[current_user] = lambda: CurrentUser(user_id="bob", is_admin=False)
    try:
        post = await client.post("/api/client-logs", json={"level": "error", "message": "bob-err"})
        assert post.status_code == 201
        resp = await client.get("/api/client-logs")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
