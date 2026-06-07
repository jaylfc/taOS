"""Tests for /api/desktop/browser/capabilities HTTP CRUD endpoints."""
from __future__ import annotations

import pytest


def _get_user_id(app):
    """Resolve the authed admin user id from the app state."""
    auth_mgr = app.state.auth
    record = auth_mgr.find_user("admin")
    return record["id"] if record else "test-admin"


def _make_auth_client(app, tmp_data_dir):
    """Create a second authenticated async client for multi-user isolation tests."""
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
class TestCapabilityAuth:
    async def test_get_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.get(
                "/api/desktop/browser/capabilities",
                params={"profile_id": "personal"},
            )
            assert resp.status_code == 401

    async def test_post_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.post(
                "/api/desktop/browser/capabilities",
                json={
                    "profile_id": "personal",
                    "agent_id": "a1",
                    "host_pattern": "example.com",
                    "permissions": "read_dom",
                },
            )
            assert resp.status_code == 401

    async def test_delete_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            resp = await c.delete(
                "/api/desktop/browser/capabilities",
                params={
                    "profile_id": "personal",
                    "agent_id": "a1",
                    "host_pattern": "example.com",
                },
            )
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 1: GET happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestListCapabilities:
    async def test_get_happy_path_returns_grant(self, client, app):
        user_id = _get_user_id(app)
        store = app.state.browser_store
        await store.add_capability(
            user_id=user_id,
            profile_id="p1",
            agent_id="agent-a",
            host_pattern="example.com",
            permissions="read_dom,navigate",
        )
        resp = await client.get(
            "/api/desktop/browser/capabilities",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "grants" in body
        assert len(body["grants"]) == 1
        grant = body["grants"][0]
        assert grant["agent_id"] == "agent-a"
        assert grant["host_pattern"] == "example.com"
        assert grant["permissions"] == "read_dom,navigate"

    # Test 2: GET filter by agent_id
    async def test_get_filter_by_agent_id(self, client, app):
        user_id = _get_user_id(app)
        store = app.state.browser_store
        await store.add_capability(
            user_id=user_id, profile_id="p1", agent_id="agent-x",
            host_pattern="*.site.com", permissions="drive",
        )
        await store.add_capability(
            user_id=user_id, profile_id="p1", agent_id="agent-y",
            host_pattern="*.site.com", permissions="navigate",
        )
        resp = await client.get(
            "/api/desktop/browser/capabilities",
            params={"profile_id": "p1", "agent_id": "agent-x"},
        )
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["agent_id"] == "agent-x"

    # Test 4: GET multi-user isolation
    async def test_get_multi_user_isolation(self, client, app):
        store = app.state.browser_store
        await store.add_capability(
            user_id="other-user", profile_id="p1", agent_id="secret-agent",
            host_pattern="*", permissions="drive",
        )
        resp = await client.get(
            "/api/desktop/browser/capabilities",
            params={"profile_id": "p1"},
        )
        assert resp.status_code == 200
        grants = resp.json()["grants"]
        assert all(g["agent_id"] != "secret-agent" for g in grants)


# ---------------------------------------------------------------------------
# Test 5–10: POST (grant)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGrantCapability:
    async def test_post_happy_path(self, client, app):
        resp = await client.post(
            "/api/desktop/browser/capabilities",
            json={
                "profile_id": "p1",
                "agent_id": "agent-g",
                "host_pattern": "foo.com",
                "permissions": "read_dom",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"granted": True}

        # GET shows the row
        get_resp = await client.get(
            "/api/desktop/browser/capabilities",
            params={"profile_id": "p1", "agent_id": "agent-g"},
        )
        grants = get_resp.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["host_pattern"] == "foo.com"
        assert grants[0]["permissions"] == "read_dom"

    # Test 6: UPSERT — same (agent, host_pattern), different permissions
    async def test_post_upsert_updates_permissions(self, client, app):
        base = {
            "profile_id": "p1",
            "agent_id": "agent-up",
            "host_pattern": "bar.com",
        }
        await client.post(
            "/api/desktop/browser/capabilities",
            json={**base, "permissions": "read_dom"},
        )
        await client.post(
            "/api/desktop/browser/capabilities",
            json={**base, "permissions": "read_dom,drive"},
        )
        get_resp = await client.get(
            "/api/desktop/browser/capabilities",
            params={"profile_id": "p1", "agent_id": "agent-up"},
        )
        grants = get_resp.json()["grants"]
        assert len(grants) == 1
        assert grants[0]["permissions"] == "read_dom,drive"

    # Test 7: 400 on empty permissions
    async def test_post_empty_permissions_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/capabilities",
            json={
                "profile_id": "p1",
                "agent_id": "a1",
                "host_pattern": "x.com",
                "permissions": "",
            },
        )
        assert resp.status_code == 400
        assert "permissions" in resp.json().get("error", "").lower()

    # Test 8: 400 on unknown permission
    async def test_post_unknown_permission_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/capabilities",
            json={
                "profile_id": "p1",
                "agent_id": "a1",
                "host_pattern": "x.com",
                "permissions": "drive,unicorn",
            },
        )
        assert resp.status_code == 400
        assert "unicorn" in resp.json().get("error", "")

    # Test 9: 400 on whitespace-only permissions
    async def test_post_whitespace_permissions_returns_400(self, client):
        resp = await client.post(
            "/api/desktop/browser/capabilities",
            json={
                "profile_id": "p1",
                "agent_id": "a1",
                "host_pattern": "x.com",
                "permissions": "   ",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 11–14: DELETE (revoke)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestRevokeCapability:
    async def test_delete_happy_path(self, client, app):
        user_id = _get_user_id(app)
        store = app.state.browser_store
        await store.add_capability(
            user_id=user_id, profile_id="p1", agent_id="agent-del",
            host_pattern="del.com", permissions="navigate",
        )
        resp = await client.delete(
            "/api/desktop/browser/capabilities",
            params={
                "profile_id": "p1",
                "agent_id": "agent-del",
                "host_pattern": "del.com",
            },
        )
        assert resp.status_code == 204

        get_resp = await client.get(
            "/api/desktop/browser/capabilities",
            params={"profile_id": "p1", "agent_id": "agent-del"},
        )
        assert get_resp.json()["grants"] == []

    # Test 12: DELETE on missing returns 204 (info-hide)
    async def test_delete_missing_returns_204(self, client):
        resp = await client.delete(
            "/api/desktop/browser/capabilities",
            params={
                "profile_id": "p1",
                "agent_id": "never-existed",
                "host_pattern": "ghost.com",
            },
        )
        assert resp.status_code == 204

    # Test 14: DELETE multi-user isolation
    async def test_delete_multi_user_isolation(self, client, app, tmp_path):
        user_a_id = _get_user_id(app)
        store = app.state.browser_store
        await store.add_capability(
            user_id=user_a_id, profile_id="p1", agent_id="agent-iso",
            host_pattern="iso.com", permissions="see_cookies",
        )

        # User B deletes — should not remove user A's row
        async with _make_auth_client(app, tmp_path) as b_client:
            resp = await b_client.delete(
                "/api/desktop/browser/capabilities",
                params={
                    "profile_id": "p1",
                    "agent_id": "agent-iso",
                    "host_pattern": "iso.com",
                },
            )
            assert resp.status_code == 204

        # User A's grant still present
        grants = await store.list_capabilities(
            user_id=user_a_id, profile_id="p1", agent_id="agent-iso",
        )
        assert len(grants) == 1
        assert grants[0]["host_pattern"] == "iso.com"
