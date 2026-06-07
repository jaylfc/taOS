"""Tests for /api/desktop/browser/site-permissions HTTP CRUD endpoints."""
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
        auth_mgr.complete_invite("user_b", invite_code, "user_b", "", "pass_b_ok")
    record = auth_mgr.find_user("user_b")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


# ---------------------------------------------------------------------------
# Auth (401) tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSitePermissionAuth:
    async def test_get_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/desktop/browser/site-permissions",
                params={"profile_id": "personal"},
            )
            assert resp.status_code == 401

    async def test_post_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post(
                "/api/desktop/browser/site-permissions",
                json={
                    "profile_id": "personal",
                    "host_pattern": "example.com",
                    "permission": "camera",
                    "state": "allow",
                },
            )
            assert resp.status_code == 401

    async def test_delete_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.delete(
                "/api/desktop/browser/site-permissions",
                params={
                    "profile_id": "personal",
                    "host_pattern": "example.com",
                    "permission": "camera",
                },
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListSitePermissions:
    async def test_get_happy_path_returns_grants(self, client, app):
        user_id = _get_user_id(app)
        store = app.state.browser_store
        await store.set_site_permission(
            user_id=user_id, profile_id="p1",
            host_pattern="example.com", permission="notifications", state="allow",
        )
        resp = await client.get(
            "/api/desktop/browser/site-permissions",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "grants" in body
        assert len(body["grants"]) == 1
        grant = body["grants"][0]
        assert grant["host_pattern"] == "example.com"
        assert grant["permission"] == "notifications"
        assert grant["state"] == "allow"

    async def test_get_empty_profile_returns_empty_list(self, client):
        resp = await client.get(
            "/api/desktop/browser/site-permissions",
            params={"profile_id": "empty-profile"},
        )
        assert resp.status_code == 200
        assert resp.json()["grants"] == []

    async def test_get_multi_user_isolation(self, client, app):
        store = app.state.browser_store
        await store.set_site_permission(
            user_id="other-user", profile_id="p1",
            host_pattern="secret.com", permission="microphone", state="allow",
        )
        resp = await client.get(
            "/api/desktop/browser/site-permissions",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        assert all(g["host_pattern"] != "secret.com" for g in grants)


# ---------------------------------------------------------------------------
# POST (set/upsert)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSetSitePermission:
    async def test_post_happy_path(self, client, app):
        resp = await client.post(
            "/api/desktop/browser/site-permissions",
            json={
                "profile_id": "p1",
                "host_pattern": "foo.com",
                "permission": "camera",
                "state": "allow",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"granted": True}

        # Verify row exists via GET
        get_resp = await client.get(
            "/api/desktop/browser/site-permissions",
            params={"profile_id": "p1"},
        )
        grants = get_resp.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["host_pattern"] == "foo.com"
        assert grants[0]["state"] == "allow"

    async def test_post_upsert_updates_state(self, client, app):
        base = {
            "profile_id": "p1",
            "host_pattern": "upsert.com",
            "permission": "geolocation",
        }
        await client.post(
            "/api/desktop/browser/site-permissions",
            json={**base, "state": "allow"},
        )
        await client.post(
            "/api/desktop/browser/site-permissions",
            json={**base, "state": "deny"},
        )
        get_resp = await client.get(
            "/api/desktop/browser/site-permissions",
            params={"profile_id": "p1"},
        )
        grants = get_resp.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["state"] == "deny"

    async def test_post_unknown_permission_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/site-permissions",
            json={
                "profile_id": "p1",
                "host_pattern": "x.com",
                "permission": "unicorn",
                "state": "allow",
            },
        )
        assert resp.status_code == 400
        assert "unicorn" in resp.json().get("error", "")

    async def test_post_invalid_state_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/site-permissions",
            json={
                "profile_id": "p1",
                "host_pattern": "x.com",
                "permission": "camera",
                "state": "maybe",
            },
        )
        assert resp.status_code == 400
        assert "state" in resp.json().get("error", "").lower()

    async def test_post_all_known_permissions_accepted(self, client):
        known = [
            "notifications", "clipboard-read", "clipboard-write",
            "geolocation", "camera", "microphone",
        ]
        for perm in known:
            resp = await client.post(
                "/api/desktop/browser/site-permissions",
                json={
                    "profile_id": "p1",
                    "host_pattern": "test.com",
                    "permission": perm,
                    "state": "allow",
                },
            )
            assert resp.status_code == 200, f"failed for permission: {perm}"


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRemoveSitePermission:
    async def test_delete_happy_path_returns_204(self, client, app):
        user_id = _get_user_id(app)
        store = app.state.browser_store
        await store.set_site_permission(
            user_id=user_id, profile_id="p1",
            host_pattern="del.com", permission="camera", state="allow",
        )
        resp = await client.delete(
            "/api/desktop/browser/site-permissions",
            params={
                "profile_id": "p1",
                "host_pattern": "del.com",
                "permission": "camera",
            },
        )
        assert resp.status_code == 204
        rows = await store.list_site_permissions(user_id=user_id, profile_id="p1")
        assert rows == []

    async def test_delete_missing_returns_204(self, client):
        resp = await client.delete(
            "/api/desktop/browser/site-permissions",
            params={
                "profile_id": "p1",
                "host_pattern": "never-existed.com",
                "permission": "microphone",
            },
        )
        assert resp.status_code == 204

    async def test_delete_multi_user_isolation(self, client, app, tmp_path):
        user_a_id = _get_user_id(app)
        store = app.state.browser_store
        await store.set_site_permission(
            user_id=user_a_id, profile_id="p1",
            host_pattern="iso.com", permission="geolocation", state="allow",
        )

        # User B deletes — should not remove user A's row
        async with _make_auth_client(app, tmp_path) as b_client:
            resp = await b_client.delete(
                "/api/desktop/browser/site-permissions",
                params={
                    "profile_id": "p1",
                    "host_pattern": "iso.com",
                    "permission": "geolocation",
                },
            )
            assert resp.status_code == 204

        rows = await store.list_site_permissions(user_id=user_a_id, profile_id="p1")
        assert len(rows) == 1
        assert rows[0]["host_pattern"] == "iso.com"
