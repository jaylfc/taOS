"""Tests for /api/desktop/browser/bookmarks HTTP CRUD endpoints."""
from __future__ import annotations

import pytest


def _get_user_id(app):
    """Resolve the authed admin user id from the app state."""
    auth_mgr = app.state.auth
    record = auth_mgr.find_user("admin")
    return record["id"] if record else "test-admin"


def _make_auth_client(app, tmp_data_dir):
    """Create an authenticated async client for a second user (user_b)."""
    from httpx import ASGITransport, AsyncClient
    auth_mgr = app.state.auth
    if auth_mgr.find_user("user_b") is None:
        invite_code = auth_mgr.add_user_invite("user_b", "admin")
        auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b")
    record = auth_mgr.find_user("user_b")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestBookmarkAuth:
    async def test_get_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/desktop/browser/bookmarks",
                params={"profile_id": "personal"},
            )
            assert resp.status_code == 401

    async def test_post_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post(
                "/api/desktop/browser/bookmarks",
                json={"profile_id": "personal", "url": "https://example.com", "title": "Example"},
            )
            assert resp.status_code == 401

    async def test_delete_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.delete(
                "/api/desktop/browser/bookmarks/some-id",
                params={"profile_id": "personal"},
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListBookmarks:
    async def test_get_empty_for_new_profile(self, client):
        resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "personal"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"bookmarks": []}

    async def test_get_returns_added_bookmark(self, client):
        await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p1", "url": "https://example.com", "title": "Example"},
        )
        resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 200
        bookmarks = resp.json()["bookmarks"]
        assert len(bookmarks) == 1
        assert bookmarks[0]["url"] == "https://example.com"
        assert bookmarks[0]["title"] == "Example"

    async def test_get_multi_user_isolation(self, client, app):
        """A bookmark seeded for another user must not appear in the authed user's list."""
        store = app.state.browser_store
        await store.create_bookmark(
            user_id="other-user", profile_id="p1",
            url="https://secret.com", title="Secret",
        )
        resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 200
        assert resp.json()["bookmarks"] == []

    async def test_get_multi_profile_isolation(self, client):
        """Bookmarks for profile A must not appear under profile B."""
        await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "profile-a", "url": "https://a.com", "title": "A"},
        )
        resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "profile-b"},
        )
        assert resp.status_code == 200
        assert resp.json()["bookmarks"] == []

    async def test_get_returns_most_recent_first(self, client):
        """List is ordered by created_at DESC (most recent first)."""
        await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p-order", "url": "https://first.com", "title": "First"},
        )
        await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p-order", "url": "https://second.com", "title": "Second"},
        )
        resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "p-order"},
        )
        bookmarks = resp.json()["bookmarks"]
        assert len(bookmarks) == 2
        # Most recent (second added) comes first
        assert bookmarks[0]["url"] == "https://second.com"


# ---------------------------------------------------------------------------
# POST tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAddBookmark:
    async def test_post_creates_bookmark_returns_id(self, client):
        resp = await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p1", "url": "https://example.com", "title": "Example"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "bookmark_id" in body
        assert isinstance(body["bookmark_id"], str)
        assert len(body["bookmark_id"]) > 0

    async def test_post_bookmark_id_is_opaque(self, client):
        """bookmark_id should be a URL-safe token, not a UUID or sequential int."""
        resp = await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p1", "url": "https://opaque.com", "title": "Opaque"},
        )
        bookmark_id = resp.json()["bookmark_id"]
        # token_urlsafe(12) produces 16-char base64url strings
        assert len(bookmark_id) == 16

    async def test_post_empty_url_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p1", "url": "", "title": "No URL"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_post_empty_title_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p1", "url": "https://example.com", "title": ""},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    async def test_post_bookmark_appears_in_list(self, client):
        await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p-verify", "url": "https://verify.com", "title": "Verify"},
        )
        resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "p-verify"},
        )
        bookmarks = resp.json()["bookmarks"]
        assert any(b["url"] == "https://verify.com" for b in bookmarks)


# ---------------------------------------------------------------------------
# DELETE tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDeleteBookmark:
    async def test_delete_returns_204_and_removes_bookmark(self, client):
        post_resp = await client.post(
            "/api/desktop/browser/bookmarks",
            json={"profile_id": "p1", "url": "https://del.com", "title": "Del"},
        )
        bookmark_id = post_resp.json()["bookmark_id"]

        del_resp = await client.delete(
            f"/api/desktop/browser/bookmarks/{bookmark_id}",
            params={"profile_id": "p1"},
        )
        assert del_resp.status_code == 204

        # Bookmark must be gone
        list_resp = await client.get(
            "/api/desktop/browser/bookmarks",
            params={"profile_id": "p1"},
        )
        bookmarks = list_resp.json()["bookmarks"]
        assert not any(b["bookmark_id"] == bookmark_id for b in bookmarks)

    async def test_delete_missing_bookmark_returns_204(self, client):
        """Info-hide: DELETE on a non-existent bookmark returns 204."""
        resp = await client.delete(
            "/api/desktop/browser/bookmarks/does-not-exist",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 204

    async def test_delete_multi_user_isolation(self, client, app, tmp_path):
        """User B cannot delete user A's bookmarks."""
        user_a_id = _get_user_id(app)
        store = app.state.browser_store
        bookmark_id = await store.create_bookmark(
            user_id=user_a_id, profile_id="p1",
            url="https://protected.com", title="Protected",
        )

        # User B tries to delete it — result is 204 but bookmark stays for A
        async with _make_auth_client(app, tmp_path) as b_client:
            resp = await b_client.delete(
                f"/api/desktop/browser/bookmarks/{bookmark_id}",
                params={"profile_id": "p1"},
            )
            assert resp.status_code == 204

        # User A's bookmark still exists
        bookmarks = await store.list_bookmarks_for_profile(
            user_id=user_a_id, profile_id="p1",
        )
        assert any(b["bookmark_id"] == bookmark_id for b in bookmarks)
