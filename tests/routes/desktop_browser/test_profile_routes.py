"""Tests for /api/desktop/browser/profiles full CRUD."""
from __future__ import annotations

import pytest


def _user_id_from_app(app):
    """Resolve the conftest-authed user id for direct store seeding."""
    auth_mgr = app.state.auth
    users = auth_mgr._read_users().get("users", []) if hasattr(auth_mgr, "_read_users") else []
    return users[0]["id"] if users else "test-admin"


@pytest.mark.asyncio
class TestProfilesAuth:
    async def test_get_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/desktop/browser/profiles")
            assert resp.status_code == 401


@pytest.mark.asyncio
class TestListProfiles:
    async def test_list_returns_default_profiles_after_first_call(self, client, app):
        # Trigger ensure_default_profiles by hitting any auth-bound endpoint
        # that calls it (the proxy does); for this test we hit profiles
        # directly — endpoint must auto-bootstrap on first GET.
        resp = await client.get("/api/desktop/browser/profiles")
        assert resp.status_code == 200
        body = resp.json()
        names = {p["name"] for p in body["profiles"]}
        assert names == {"Personal", "Work"}


@pytest.mark.asyncio
class TestCreateProfile:
    async def test_post_creates_profile(self, client):
        resp = await client.post(
            "/api/desktop/browser/profiles",
            json={"name": "Research", "color": "#88aa44"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Research"
        assert body["color"] == "#88aa44"
        assert body["profile_id"] == "research"

    async def test_post_appends_suffix_on_collision(self, client):
        await client.post(
            "/api/desktop/browser/profiles", json={"name": "Personal", "color": "#000000"},
        )
        body = (await client.post(
            "/api/desktop/browser/profiles", json={"name": "Personal", "color": "#111111"},
        )).json()
        # Original "personal" exists from defaults; first POST gets "personal-2"
        assert body["profile_id"] in {"personal-2", "personal-3"}


@pytest.mark.asyncio
class TestPatchProfile:
    async def test_patch_renames_profile(self, client):
        resp = await client.patch(
            "/api/desktop/browser/profiles/personal",
            json={"name": "My Profile"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Profile"

    async def test_patch_changes_color(self, client):
        resp = await client.patch(
            "/api/desktop/browser/profiles/personal",
            json={"color": "#abcdef"},
        )
        assert resp.status_code == 200
        assert resp.json()["color"] == "#abcdef"

    async def test_patch_missing_returns_404(self, client):
        resp = await client.patch(
            "/api/desktop/browser/profiles/nonexistent",
            json={"name": "X"},
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestDeleteProfile:
    async def test_delete_returns_204_and_removes(self, client):
        # Create a fresh deletable profile (the defaults can't be deleted
        # if they're the last two — see refuses-last-profile test below)
        await client.post(
            "/api/desktop/browser/profiles",
            json={"name": "Temp", "color": "#999999"},
        )

        resp = await client.delete("/api/desktop/browser/profiles/temp")
        assert resp.status_code == 204

        # List shouldn't include it anymore
        list_resp = await client.get("/api/desktop/browser/profiles")
        ids = {p["profile_id"] for p in list_resp.json()["profiles"]}
        assert "temp" not in ids

    async def test_delete_cascades_cookies(self, client, app):
        await client.post(
            "/api/desktop/browser/profiles",
            json={"name": "Cookie Test", "color": "#cc0000"},
        )

        # Seed a cookie under that profile via the store directly
        cookie_store = app.state.browser_cookie_store
        user_id = _user_id_from_app(app)
        await cookie_store.set_cookie(
            user_id=user_id, profile_id="cookie-test",
            host="example.com", path="/", name="sid", value="xyz",
            expires_at=None, http_only=False, secure=False, same_site=None,
        )
        before = await cookie_store.get_cookies(
            user_id=user_id, profile_id="cookie-test", host="example.com",
        )
        assert len(before) == 1

        # Delete the profile
        resp = await client.delete("/api/desktop/browser/profiles/cookie-test")
        assert resp.status_code == 204

        # Cookies for that profile must be gone
        after = await cookie_store.get_cookies(
            user_id=user_id, profile_id="cookie-test", host="example.com",
        )
        assert after == []

    async def test_delete_missing_returns_404(self, client):
        resp = await client.delete("/api/desktop/browser/profiles/never-existed")
        assert resp.status_code == 404

    async def test_delete_refuses_last_profile(self, client):
        # First delete one of the two defaults
        await client.delete("/api/desktop/browser/profiles/work")
        # Trying to delete the only remaining profile must 400
        resp = await client.delete("/api/desktop/browser/profiles/personal")
        assert resp.status_code == 400
        assert "last" in resp.json().get("error", "").lower()


@pytest.mark.asyncio
class TestMultiUserIsolation:
    async def test_patch_other_users_profile_returns_404(self, client, app):
        # Seed a profile for a DIFFERENT user
        store = app.state.browser_store
        import time
        await store.add_profile(
            user_id="other-user", profile_id="secret",
            name="Secret", color="#000", created_at=int(time.time()),
        )

        # Authed user should NOT see / be able to patch other-user's profile
        resp = await client.patch(
            "/api/desktop/browser/profiles/secret", json={"name": "X"},
        )
        assert resp.status_code == 404
