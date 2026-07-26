"""Tests for POST/GET /api/feedback endpoints."""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient

import pytest
import pytest_asyncio

from tinyagentos.feedback_store import FeedbackStore, MAX_FEEDBACK_PER_USER_PER_DAY, MAX_SCREENSHOT_LEN


# ---------------------------------------------------------------------------
# Store-level unit tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path):
    s = FeedbackStore(tmp_path / "feedback.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_store_create_and_list(store):
    item = await store.create(
        user_id="u1",
        type="bug",
        title="Something broke",
        body="Details here",
    )
    assert item["id"]
    assert item["type"] == "bug"
    assert item["created_at"]

    items = await store.list_for_user("u1")
    assert len(items) == 1
    assert items[0]["id"] == item["id"]
    # List endpoint must NOT include the screenshot blob
    assert "screenshot" not in items[0]
    assert "has_screenshot" in items[0]
    assert items[0]["has_screenshot"] is False


@pytest.mark.asyncio
async def test_store_get_by_id_includes_screenshot(store):
    await store.create(
        user_id="u1",
        type="feature",
        title="Dark mode",
        body="",
        screenshot="data:image/png;base64,abc123",
    )
    items = await store.list_for_user("u1")
    full = await store.get_by_id(items[0]["id"], "u1")
    assert full is not None
    assert full["screenshot"] == "data:image/png;base64,abc123"
    assert full["has_screenshot"] is True


@pytest.mark.asyncio
async def test_store_user_isolation(store):
    await store.create(user_id="u1", type="bug", title="User 1 bug", body="")
    await store.create(user_id="u2", type="feature", title="User 2 feature", body="")

    u1_items = await store.list_for_user("u1")
    u2_items = await store.list_for_user("u2")
    assert len(u1_items) == 1
    assert len(u2_items) == 1
    assert u1_items[0]["title"] == "User 1 bug"
    assert u2_items[0]["title"] == "User 2 feature"


# ---------------------------------------------------------------------------
# Route-level tests via the async HTTP client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_feedback_creates_submission(client):
    resp = await client.post(
        "/api/feedback",
        json={"type": "bug", "title": "Login fails", "body": "Cannot sign in"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "bug"
    assert data["title"] == "Login fails"
    assert "id" in data
    assert "created_at" in data
    assert "screenshot" not in data
    assert data["has_screenshot"] is False


@pytest.mark.asyncio
async def test_get_feedback_lists_submissions(client):
    await client.post(
        "/api/feedback",
        json={"type": "feature", "title": "Add dark mode", "body": ""},
    )
    resp = await client.get("/api/feedback")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert items[0]["title"] == "Add dark mode"
    assert "screenshot" not in items[0]


@pytest.mark.asyncio
async def test_get_feedback_by_id_returns_screenshot(client):
    screenshot = "data:image/png;base64," + "A" * 100
    post_resp = await client.post(
        "/api/feedback",
        json={"type": "bug", "title": "Visual glitch", "body": "", "screenshot": screenshot},
    )
    item_id = post_resp.json()["id"]

    resp = await client.get(f"/api/feedback/{item_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["screenshot"] == screenshot
    assert data["has_screenshot"] is True


@pytest.mark.asyncio
async def test_invalid_type_rejected(client):
    resp = await client.post(
        "/api/feedback",
        json={"type": "complaint", "title": "Bad type", "body": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_title_rejected(client):
    resp = await client.post(
        "/api/feedback",
        json={"type": "bug", "title": "   ", "body": ""},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_oversized_screenshot_rejected(client):
    big_screenshot = "data:image/png;base64," + "A" * (MAX_SCREENSHOT_LEN + 1)
    resp = await client.post(
        "/api/feedback",
        json={"type": "bug", "title": "Big screenshot", "body": "", "screenshot": big_screenshot},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_unknown_id_returns_404(client):
    resp = await client.get("/api/feedback/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_feedback_rate_limit_exceeded(client):
    for i in range(MAX_FEEDBACK_PER_USER_PER_DAY):
        resp = await client.post(
            "/api/feedback",
            json={"type": "bug", "title": f"Bug {i}", "body": ""},
        )
        assert resp.status_code == 201
    resp = await client.post(
        "/api/feedback",
        json={"type": "bug", "title": "Over limit", "body": ""},
    )
    assert resp.status_code == 429
    assert "Too many" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_post_feedback_rate_limit_per_user(client, app):
    for i in range(MAX_FEEDBACK_PER_USER_PER_DAY):
        resp = await client.post(
            "/api/feedback",
            json={"type": "bug", "title": f"Admin bug {i}", "body": ""},
        )
        assert resp.status_code == 201

    invite_code = app.state.auth.add_user_invite("user2", "admin")
    app.state.auth.complete_invite("user2", invite_code, "User Two", "", "password")
    record = app.state.auth.find_user("user2")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as user2_client:
        resp = await user2_client.post(
            "/api/feedback",
            json={"type": "feature", "title": "User2 feature", "body": ""},
        )
        assert resp.status_code == 201
