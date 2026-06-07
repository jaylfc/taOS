"""Tests for push_mutes table, store methods, and HTTP API."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Store-level fixture (no app needed)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserStore

    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# HTTP helper: create an authenticated client for a second user
# ---------------------------------------------------------------------------


def _make_user_b_client(app):
    auth_mgr = app.state.auth
    if auth_mgr.find_user("user_b") is None:
        invite_code = auth_mgr.add_user_invite("user_b", "admin")
        auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b_ok")
    record = auth_mgr.find_user("user_b")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


# ---------------------------------------------------------------------------
# 1. Set then list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_then_list(client):
    resp = await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "chat", "muted": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    list_resp = await client.get("/api/desktop/browser/push/mutes")
    assert list_resp.status_code == 200
    mutes = list_resp.json()["mutes"]
    assert len(mutes) == 1
    assert mutes[0]["agent_id"] == "agent-A"
    assert mutes[0]["kind"] == "chat"
    assert isinstance(mutes[0]["muted_at"], int)


# ---------------------------------------------------------------------------
# 2. Unmute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmute(client):
    await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "chat", "muted": True},
    )
    resp = await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "chat", "muted": False},
    )
    assert resp.status_code == 200

    list_resp = await client.get("/api/desktop/browser/push/mutes")
    assert list_resp.json()["mutes"] == []


# ---------------------------------------------------------------------------
# 3. Multi-user isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multi_user_isolation(client, app):
    # User A (current client) sets a mute
    await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "chat", "muted": True},
    )

    # User B's GET must be empty
    async with _make_user_b_client(app) as b_client:
        resp_b = await b_client.get("/api/desktop/browser/push/mutes")
        assert resp_b.status_code == 200
        assert resp_b.json()["mutes"] == []

        # User B sets their own mute
        await b_client.put(
            "/api/desktop/browser/push/mutes",
            json={"agent_id": "agent-B", "kind": "drive-started", "muted": True},
        )

    # User A's list must not contain user B's mute
    list_resp_a = await client.get("/api/desktop/browser/push/mutes")
    mutes_a = list_resp_a.json()["mutes"]
    assert all(m["agent_id"] != "agent-B" for m in mutes_a)
    assert len(mutes_a) == 1


# ---------------------------------------------------------------------------
# 4. Invalid kind → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_kind_returns_422(client):
    resp = await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "explosion", "muted": True},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. Auth required on GET and PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mutes_unauthenticated_returns_401(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/api/desktop/browser/push/mutes")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_put_mutes_unauthenticated_returns_401(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.put(
            "/api/desktop/browser/push/mutes",
            json={"agent_id": "agent-A", "kind": "chat", "muted": True},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. is_push_muted direct store check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_push_muted_store(store):
    # Initially not muted
    assert await store.is_push_muted("user_a", "agent-X", "chat") is False

    # Set mute
    await store.set_push_mute("user_a", "agent-X", "chat", True)
    assert await store.is_push_muted("user_a", "agent-X", "chat") is True

    # Different kind for same agent → False
    assert await store.is_push_muted("user_a", "agent-X", "drive-started") is False

    # Unset mute
    await store.set_push_mute("user_a", "agent-X", "chat", False)
    assert await store.is_push_muted("user_a", "agent-X", "chat") is False


# ---------------------------------------------------------------------------
# 7. Replace on duplicate set (PRIMARY KEY enforced)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_on_duplicate_set(client):
    await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "chat", "muted": True},
    )
    await client.put(
        "/api/desktop/browser/push/mutes",
        json={"agent_id": "agent-A", "kind": "chat", "muted": True},
    )

    list_resp = await client.get("/api/desktop/browser/push/mutes")
    mutes = list_resp.json()["mutes"]
    assert len(mutes) == 1
