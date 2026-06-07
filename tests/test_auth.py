from __future__ import annotations

import stat
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import (
    AuthManager,
    hash_password,
    verify_password,
    verify_and_maybe_rehash,
    _hash_password_sha256,
)


# --- Unit tests for password hashing ---

class TestPasswordHashing:
    def test_hash_produces_argon2_hash(self):
        result = hash_password("secret")
        assert result.startswith("$argon2")

    def test_hash_with_salt_param_ignored(self):
        # salt param is accepted for backward-compat but ignored; result is still argon2
        result = hash_password("secret", "abcd1234")
        assert result.startswith("$argon2")

    def test_verify_correct_password(self):
        stored = hash_password("mypassword")
        assert verify_password("mypassword", stored) is True

    def test_verify_wrong_password(self):
        stored = hash_password("mypassword")
        assert verify_password("wrong", stored) is False

    def test_different_passwords_different_hashes(self):
        h1 = hash_password("alpha")
        h2 = hash_password("beta")
        assert h1 != h2

    def test_verify_legacy_sha256_hash(self):
        """Legacy SHA-256 hashes are still verified correctly."""
        legacy = _hash_password_sha256("mypassword", "somesalt")
        assert verify_password("mypassword", legacy) is True
        assert verify_password("wrong", legacy) is False

    def test_verify_and_maybe_rehash_upgrades_sha256(self):
        """verify_and_maybe_rehash returns a new argon2 hash for old SHA-256 stored values."""
        legacy = _hash_password_sha256("mypassword", "somesalt")
        ok, new_hash = verify_and_maybe_rehash("mypassword", legacy)
        assert ok is True
        assert new_hash is not None
        assert new_hash.startswith("$argon2")

    def test_verify_and_maybe_rehash_no_upgrade_for_argon2(self):
        stored = hash_password("mypassword")
        ok, new_hash = verify_and_maybe_rehash("mypassword", stored)
        assert ok is True
        assert new_hash is None  # already argon2, no upgrade needed

    def test_verify_and_maybe_rehash_wrong_password(self):
        stored = hash_password("mypassword")
        ok, new_hash = verify_and_maybe_rehash("wrong", stored)
        assert ok is False
        assert new_hash is None


# --- Unit tests for AuthManager ---

class TestAuthManager:
    def test_not_configured_initially(self, tmp_path):
        mgr = AuthManager(tmp_path)
        assert mgr.is_configured() is False

    def test_configured_after_set_password(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.set_password("test123")
        assert mgr.is_configured() is True

    def test_check_password_correct(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.set_password("test123")
        ok, _ = mgr.check_password("test123")
        assert ok is True

    def test_check_password_wrong(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.set_password("test123")
        ok, _ = mgr.check_password("wrong")
        assert ok is False

    def test_check_password_not_configured(self, tmp_path):
        mgr = AuthManager(tmp_path)
        ok, _ = mgr.check_password("anything")
        assert ok is False

    def test_create_and_validate_session(self, tmp_path):
        mgr = AuthManager(tmp_path)
        token = mgr.create_session(user_id="uid1")
        assert mgr.validate_session(token) is not None

    def test_validate_invalid_token(self, tmp_path):
        mgr = AuthManager(tmp_path)
        assert mgr.validate_session("bogus") is None

    def test_revoke_session(self, tmp_path):
        mgr = AuthManager(tmp_path)
        token = mgr.create_session(user_id="uid1")
        mgr.revoke_session(token)
        assert mgr.validate_session(token) is None

    def test_revoke_nonexistent_session(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.revoke_session("nonexistent")  # should not raise

    def test_expired_session(self, tmp_path):
        mgr = AuthManager(tmp_path)
        token = mgr.create_session(user_id="uid1")
        # Manually expire it — use dict format matching new schema
        mgr._sessions[token] = {"user_id": "uid1", "expires_at": time.time() - 1, "long_lived": False}
        assert mgr.validate_session(token) is None
        # Should also be cleaned up
        assert token not in mgr._sessions

    def test_cleanup_sessions(self, tmp_path):
        mgr = AuthManager(tmp_path)
        t1 = mgr.create_session(user_id="uid1")
        t2 = mgr.create_session(user_id="uid2")
        # Expire t1 using dict format
        mgr._sessions[t1] = {"user_id": "uid1", "expires_at": time.time() - 1, "long_lived": False}
        mgr.cleanup_sessions()
        assert t1 not in mgr._sessions
        assert t2 in mgr._sessions


# --- Integration tests for routes ---

@pytest_asyncio.fixture
async def auth_client(app):
    """Client for auth tests — initialises required stores."""
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    notif_store = app.state.notifications
    if notif_store._db is not None:
        await notif_store.close()
    await notif_store.init()
    await app.state.qmd_client.init()
    secrets_store = app.state.secrets
    if secrets_store._db is not None:
        await secrets_store.close()
    await secrets_store.init()
    scheduler = app.state.scheduler
    if scheduler._db is not None:
        await scheduler.close()
    await scheduler.init()
    channel_store = app.state.channels
    if channel_store._db is not None:
        await channel_store.close()
    await channel_store.init()
    relationship_mgr = app.state.relationships
    if relationship_mgr._db is not None:
        await relationship_mgr.close()
    await relationship_mgr.init()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await relationship_mgr.close()
    await channel_store.close()
    await scheduler.close()
    await secrets_store.close()
    await notif_store.close()
    await store.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


class TestAuthRoutes:
    @pytest.mark.asyncio
    async def test_login_page_accessible(self, app, auth_client):
        # Auth must be configured so the route renders the login form instead
        # of redirecting to /auth/setup.
        app.state.auth.setup_user("admin", "Admin", "", "adminpass")
        resp = await auth_client.get("/auth/login")
        assert resp.status_code == 200
        assert "Sign in" in resp.text

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, app, auth_client):
        app.state.auth.set_password("correctpass")
        resp = await auth_client.post(
            "/auth/login",
            data={"password": "wrong"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_login_correct_password(self, app, auth_client):
        app.state.auth.set_password("correctpass")
        resp = await auth_client.post(
            "/auth/login",
            data={"password": "correctpass"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/desktop"
        assert "taos_session" in resp.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, app, auth_client):
        app.state.auth.set_password("passw0rd")
        # Login first
        resp = await auth_client.post(
            "/auth/login",
            data={"password": "passw0rd"},
            follow_redirects=False,
        )
        cookies = resp.cookies
        # Logout
        resp = await auth_client.post("/auth/logout", cookies=cookies, follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_auth_setup_sets_password(self, app, auth_client):
        assert app.state.auth.is_configured() is False
        resp = await auth_client.post(
            "/auth/setup",
            data={"username": "admin", "full_name": "Admin", "email": "", "password": "newpassword"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert app.state.auth.is_configured() is True
        ok, _ = app.state.auth.check_password("newpassword", username="admin")
        assert ok is True

    @pytest.mark.asyncio
    async def test_auth_setup_rejects_if_already_configured(self, app, auth_client):
        app.state.auth.setup_user("admin", "Admin", "", "existing")
        resp = await auth_client.post(
            "/auth/setup",
            json={"username": "other", "full_name": "", "email": "", "password": "newpassword"},
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_auth_setup_rejects_empty_password(self, app, auth_client):
        resp = await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "", "email": "", "password": ""},
        )
        assert resp.status_code == 400


class TestAuthMiddleware:
    @pytest.mark.asyncio
    async def test_no_auth_when_not_configured(self, auth_client):
        """Before onboarding, /api/* must hard-fail so the SPA forces setup.

        Exempt paths (health, cluster heartbeat, /static/, /desktop, /auth/*)
        still pass through; everything else returns 401 with
        needs_onboarding so the client routes to OnboardingScreen instead
        of acting on stale state.
        """
        # Exempt path still works
        resp = await auth_client.get("/api/health")
        assert resp.status_code == 200

        # Non-exempt /api/* now requires onboarding
        resp = await auth_client.get("/api/system")
        assert resp.status_code == 401
        assert resp.json().get("needs_onboarding") is True

    @pytest.mark.asyncio
    async def test_protected_route_returns_401(self, app, auth_client):
        """With auth configured, API routes should return 401 without session."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.get("/api/system")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_health_exempt(self, app, auth_client):
        """Health endpoint should be accessible without auth."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.get("/api/health")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_login_page_exempt(self, app, auth_client):
        """Login page should be accessible without auth."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.get("/auth/login")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_static_exempt(self, app, auth_client):
        """Static files should be accessible without auth (404 is fine, not 401)."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.get("/static/app.css")
        # Should not be 401 — could be 200 or 404 depending on file existence
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_authenticated_request_passes(self, app, auth_client):
        """With valid session cookie, protected routes should work."""
        app.state.auth.set_password("secretpass")
        # Login to get session
        resp = await auth_client.post(
            "/auth/login",
            data={"password": "secretpass"},
            follow_redirects=False,
        )
        cookies = resp.cookies
        # Access protected route with session
        resp = await auth_client.get("/api/system", cookies=cookies)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_html_request_redirects_to_login(self, app, auth_client):
        """Browser requests should redirect to login page."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.get(
            "/",
            headers={"accept": "text/html,application/xhtml+xml"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_cluster_worker_exempt(self, app, auth_client):
        """Worker registration should be exempt from auth."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.post(
            "/api/cluster/workers",
            json={"worker_id": "test", "capabilities": {}},
        )
        # Should not be 401 (may be 422 or other depending on validation)
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_cluster_heartbeat_exempt(self, app, auth_client):
        """Worker heartbeat should be exempt from auth."""
        app.state.auth.set_password("secretpass")
        resp = await auth_client.post(
            "/api/cluster/heartbeat",
            json={"worker_id": "test"},
        )
        assert resp.status_code != 401


class TestMultiUser:
    """Multi-user invite flow, admin gates, session revocation."""

    @pytest.mark.asyncio
    async def test_first_setup_creates_admin(self, app, auth_client):
        resp = await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["user"]["is_admin"] is True

    @pytest.mark.asyncio
    async def test_admin_can_add_user(self, app, auth_client):
        # Setup admin
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        cookies = login.cookies
        resp = await auth_client.post(
            "/auth/users",
            json={"username": "alice"},
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        code = data["invite_code"]
        # token_urlsafe(16) produces 22 base64url chars
        assert len(code) >= 16

    @pytest.mark.asyncio
    async def test_pending_user_login_with_invite_code(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        cookies = login.cookies
        add = await auth_client.post(
            "/auth/users",
            json={"username": "bob"},
            cookies=cookies,
        )
        code = add.json()["invite_code"]
        # Bob logs in with the invite code as password
        resp = await auth_client.post(
            "/auth/login",
            json={"username": "bob", "password": code, "auto_login": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["needs_onboarding"] is True
        assert data["user"]["username"] == "bob"

    @pytest.mark.asyncio
    async def test_complete_invite_sets_profile_and_password(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        add = await auth_client.post(
            "/auth/users",
            json={"username": "carol"},
            cookies=login.cookies,
        )
        code = add.json()["invite_code"]
        resp = await auth_client.post(
            "/auth/complete",
            json={
                "username": "carol",
                "invite_code": code,
                "full_name": "Carol Smith",
                "email": "carol@example.com",
                "password": "carolpass",
                "auto_login": False,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Now log in with real password
        resp2 = await auth_client.post(
            "/auth/login",
            json={"username": "carol", "password": "carolpass", "auto_login": False},
        )
        assert resp2.status_code == 200
        assert resp2.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_non_admin_cannot_add_users(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login_admin = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        add = await auth_client.post(
            "/auth/users",
            json={"username": "dave"},
            cookies=login_admin.cookies,
        )
        code = add.json()["invite_code"]
        await auth_client.post(
            "/auth/complete",
            json={"username": "dave", "invite_code": code, "full_name": "Dave", "email": "", "password": "davepass", "auto_login": False},
        )
        login_dave = await auth_client.post(
            "/auth/login",
            json={"username": "dave", "password": "davepass", "auto_login": False},
        )
        resp = await auth_client.post(
            "/auth/users",
            json={"username": "newguy"},
            cookies=login_dave.cookies,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cannot_delete_self(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        resp = await auth_client.delete("/auth/users/admin", cookies=login.cookies)
        assert resp.status_code == 400
        assert "self" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_cannot_delete_last_admin(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login_admin = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        # Add a non-admin user
        add = await auth_client.post("/auth/users", json={"username": "eve"}, cookies=login_admin.cookies)
        code = add.json()["invite_code"]
        await auth_client.post(
            "/auth/complete",
            json={"username": "eve", "invite_code": code, "full_name": "Eve", "email": "", "password": "evepasswd", "auto_login": False},
        )
        # Try to delete admin (the only admin)
        resp = await auth_client.delete("/auth/users/eve", cookies=login_admin.cookies)
        assert resp.status_code == 200
        # Now try to delete self (admin) — blocked even though we just deleted eve
        resp2 = await auth_client.delete("/auth/users/admin", cookies=login_admin.cookies)
        assert resp2.status_code == 400

    @pytest.mark.asyncio
    async def test_admin_reset_password(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        add = await auth_client.post("/auth/users", json={"username": "frank"}, cookies=login.cookies)
        code = add.json()["invite_code"]
        await auth_client.post(
            "/auth/complete",
            json={"username": "frank", "invite_code": code, "full_name": "Frank", "email": "", "password": "frankpass", "auto_login": False},
        )
        resp = await auth_client.post("/auth/users/frank/reset", cookies=login.cookies)
        assert resp.status_code == 200
        new_code = resp.json()["invite_code"]
        assert len(new_code) >= 16
        # Frank can no longer log in with old password
        bad = await auth_client.post(
            "/auth/login",
            json={"username": "frank", "password": "frankpass", "auto_login": False},
        )
        assert bad.status_code == 401

    @pytest.mark.asyncio
    async def test_profile_update_self(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "old@example.com", "password": "adminpass", "auto_login": False},
        )
        login = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        resp = await auth_client.post(
            "/auth/users/admin/profile",
            json={"full_name": "Admin Updated", "email": "new@example.com"},
            cookies=login.cookies,
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["full_name"] == "Admin Updated"

    @pytest.mark.asyncio
    async def test_profile_update_other_user_forbidden(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login_admin = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        add = await auth_client.post("/auth/users", json={"username": "grace"}, cookies=login_admin.cookies)
        code = add.json()["invite_code"]
        await auth_client.post(
            "/auth/complete",
            json={"username": "grace", "invite_code": code, "full_name": "Grace", "email": "", "password": "gracepass", "auto_login": False},
        )
        login_grace = await auth_client.post(
            "/auth/login",
            json={"username": "grace", "password": "gracepass", "auto_login": False},
        )
        # Grace tries to update admin's profile
        resp = await auth_client.post(
            "/auth/users/admin/profile",
            json={"full_name": "Hacked"},
            cookies=login_grace.cookies,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_sessions_revoked_on_delete(self, app, auth_client):
        await auth_client.post(
            "/auth/setup",
            json={"username": "admin", "full_name": "Admin", "email": "", "password": "adminpass", "auto_login": False},
        )
        login_admin = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass", "auto_login": False},
        )
        add = await auth_client.post("/auth/users", json={"username": "henry"}, cookies=login_admin.cookies)
        code = add.json()["invite_code"]
        comp = await auth_client.post(
            "/auth/complete",
            json={"username": "henry", "invite_code": code, "full_name": "Henry", "email": "", "password": "henrypass", "auto_login": False},
        )
        henry_cookies = comp.cookies
        # Delete henry
        await auth_client.delete("/auth/users/henry", cookies=login_admin.cookies)
        # Henry's session should be invalid now
        resp = await auth_client.get("/api/system", cookies=henry_cookies)
        assert resp.status_code == 401


class TestLocalToken:
    def test_token_created_on_first_access(self, tmp_path):
        mgr = AuthManager(tmp_path)
        token = mgr.get_local_token()
        assert len(token) >= 32
        path = mgr.local_token_path()
        assert path.exists()
        assert path.read_text().strip() == token

    def test_token_stable_across_calls(self, tmp_path):
        mgr = AuthManager(tmp_path)
        t1 = mgr.get_local_token()
        t2 = mgr.get_local_token()
        assert t1 == t2

    def test_token_file_permissions_are_0600(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.get_local_token()
        mode = mgr.local_token_path().stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_validate_local_token_accepts_match(self, tmp_path):
        mgr = AuthManager(tmp_path)
        tok = mgr.get_local_token()
        assert mgr.validate_local_token(tok) is True

    def test_validate_local_token_rejects_mismatch(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.get_local_token()
        assert mgr.validate_local_token("wrong") is False
        assert mgr.validate_local_token("") is False


@pytest_asyncio.fixture
async def no_cookie_client(app):
    """Async client without any session cookie — for testing unauthenticated Bearer paths."""
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    notif_store = app.state.notifications
    if notif_store._db is not None:
        await notif_store.close()
    await notif_store.init()
    await app.state.qmd_client.init()
    secrets_store = app.state.secrets
    if secrets_store._db is not None:
        await secrets_store.close()
    await secrets_store.init()
    scheduler = app.state.scheduler
    if scheduler._db is not None:
        await scheduler.close()
    await scheduler.init()
    channel_store = app.state.channels
    if channel_store._db is not None:
        await channel_store.close()
    await channel_store.init()
    relationship_mgr = app.state.relationships
    if relationship_mgr._db is not None:
        await relationship_mgr.close()
    await relationship_mgr.init()
    # Configure auth so the app isn't in onboarding mode
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await relationship_mgr.close()
    await channel_store.close()
    await scheduler.close()
    await secrets_store.close()
    await notif_store.close()
    await store.close()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()


class TestMiddlewareBearerPath:
    """Middleware accepts Bearer <local-token> and rejects bad tokens."""

    @pytest.mark.asyncio
    async def test_bearer_accepted(self, app, no_cookie_client):
        tok = app.state.auth.get_local_token()
        resp = await no_cookie_client.get(
            "/api/agents",
            headers={"Authorization": f"Bearer {tok}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_bearer_bad_token_rejected(self, no_cookie_client):
        resp = await no_cookie_client.get(
            "/api/agents",
            headers={"Authorization": "Bearer nope"},
        )
        assert resp.status_code == 401


class TestSessionUserNoFallback:
    """session_user must resolve to *nobody* on a bad/empty token, unlike
    get_user (which falls back to the first user). Author-ownership checks in
    chat.py rely on this distinction, so lock the invariant in.
    """

    def test_session_user_bad_token_is_none(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "", "alicepwd1")
        assert mgr.session_user("not-a-real-token") is None

    def test_session_user_empty_token_is_none(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "", "alicepwd1")
        assert mgr.session_user("") is None

    def test_get_user_bad_token_falls_back_to_first_user(self, tmp_path):
        # Documents the footgun session_user avoids: get_user with a present
        # but invalid token returns the first user, which is wrong for
        # identity/ownership decisions.
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "", "alicepwd1")
        assert mgr.get_user(token="not-a-real-token") is not None

    def test_session_user_valid_token_returns_owner(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "", "alicepwd1")
        rec = mgr.find_user("alice")
        token = mgr.create_session(user_id=rec["id"])
        u = mgr.session_user(token)
        assert u is not None and u["id"] == rec["id"]


class TestPasswordPolicy:
    def test_complete_invite_rejects_short_password(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "adminpass")
        code = mgr.add_user_invite("bob", "admin")
        import pytest
        with pytest.raises(ValueError, match="8"):
            mgr.complete_invite("bob", code, "Bob", "", "short")

    def test_complete_invite_accepts_8_char_password(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "adminpass")
        code = mgr.add_user_invite("bob", "admin")
        user = mgr.complete_invite("bob", code, "Bob", "", "exactly8")
        assert user["username"] == "bob"

    def test_change_password_rejects_short(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "", "alicepass")
        result = mgr.change_password("alice", "alicepass", "short")
        assert result is False

    def test_setup_user_rejects_short_password(self, tmp_path):
        mgr = AuthManager(tmp_path)
        import pytest
        with pytest.raises(ValueError, match="8"):
            mgr.setup_user("admin", "Admin", "", "short")

    def test_setup_user_accepts_8_char_password(self, tmp_path):
        mgr = AuthManager(tmp_path)
        user = mgr.setup_user("admin", "Admin", "", "exactly8")
        assert user["username"] == "admin"

    def test_session_ttl_is_30_days(self, tmp_path):
        mgr = AuthManager(tmp_path)
        assert mgr.long_session_ttl == 86400 * 30


class TestInviteCodeEntropy:
    def test_invite_code_is_urlsafe_token(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "adminpass")
        code = mgr.add_user_invite("bob", "admin")
        # token_urlsafe(16) → 22 base64url chars; definitely not all digits
        assert len(code) >= 16
        assert not code.isdigit()

    def test_invite_codes_are_unique(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "adminpass")
        codes = set()
        for i in range(10):
            code = mgr.add_user_invite(f"user{i}", "admin")
            codes.add(code)
        assert len(codes) == 10  # all unique

    def test_admin_reset_gives_high_entropy_code(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "adminpass")
        code = mgr.add_user_invite("bob", "admin")
        mgr.complete_invite("bob", code, "Bob", "", "bobpasswd")
        new_code = mgr.admin_reset_password("bob", "admin")
        assert len(new_code) >= 16
        assert not new_code.isdigit()


class TestLoginRateLimit:
    # ASGITransport sends requests from 127.0.0.1
    _TEST_IP = "127.0.0.1"

    @pytest.mark.asyncio
    async def test_rate_limit_blocks_after_5_failures(self, app, auth_client):
        from tinyagentos.routes.auth import _login_limiter
        _login_limiter.reset(self._TEST_IP)
        app.state.auth.setup_user("admin", "Admin", "", "adminpass")
        # First 5 failures should each return 401 (not yet limited)
        for i in range(5):
            r = await auth_client.post(
                "/auth/login",
                json={"username": "admin", "password": "wrongpass"},
            )
            assert r.status_code == 401, f"attempt {i+1} expected 401, got {r.status_code}"
        # 6th attempt should be 429
        resp = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "wrongpass"},
        )
        _login_limiter.reset(self._TEST_IP)  # clean up after test
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_successful_login_resets_rate_limit(self, app, auth_client):
        from tinyagentos.routes.auth import _login_limiter
        _login_limiter.reset(self._TEST_IP)
        app.state.auth.setup_user("admin", "Admin", "", "adminpass")
        # 4 failures
        for _ in range(4):
            await auth_client.post(
                "/auth/login",
                json={"username": "admin", "password": "wrongpass"},
            )
        # Success resets counter
        await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "adminpass"},
        )
        # Should not be blocked on next failure
        resp = await auth_client.post(
            "/auth/login",
            json={"username": "admin", "password": "wrongpass"},
        )
        _login_limiter.reset(self._TEST_IP)
        assert resp.status_code == 401  # not 429

    @pytest.mark.asyncio
    async def test_rate_limit_form_login_redirects_not_json(self, app, auth_client):
        """Form-submitted logins that hit the rate limit must get an HTML redirect,
        not a raw JSON response (the no-JS form has no way to render JSON)."""
        from tinyagentos.routes.auth import _login_limiter
        _login_limiter.reset(self._TEST_IP)
        app.state.auth.setup_user("admin", "Admin", "", "adminpass")
        # Exhaust the limit via form posts
        for _ in range(5):
            await auth_client.post(
                "/auth/login",
                data={"username": "admin", "password": "wrongpass"},
            )
        # 6th form post should redirect (303), not return JSON 429
        resp = await auth_client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrongpass"},
        )
        _login_limiter.reset(self._TEST_IP)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers.get("location", "")


class TestFailCounterBounds:
    """Rate-limiter _FailCounter is bounded: expired entries prune, size caps."""

    def test_expired_entries_are_pruned(self):
        from tinyagentos.routes.auth import _FailCounter
        fc = _FailCounter(max_attempts=5, window_seconds=1)
        fc.record_failure("1.2.3.4")
        assert "1.2.3.4" in fc._log
        # Backdate the timestamp so it looks expired
        fc._log["1.2.3.4"] = [0.0]
        # Accessing via is_limited triggers _prune, which drops the stale entry
        assert fc.is_limited("1.2.3.4") is False
        assert "1.2.3.4" not in fc._log

    def test_size_cap_evicts_oldest(self):
        from tinyagentos.routes.auth import _FailCounter, _FAIL_COUNTER_MAX_KEYS
        fc = _FailCounter(max_attempts=5, window_seconds=600)
        # Fill to capacity
        for i in range(_FAIL_COUNTER_MAX_KEYS):
            fc.record_failure(f"10.0.{i // 256}.{i % 256}")
        assert len(fc._log) == _FAIL_COUNTER_MAX_KEYS
        # One more entry must not grow beyond the cap
        fc.record_failure("99.99.99.99")
        assert len(fc._log) == _FAIL_COUNTER_MAX_KEYS

    def test_active_entries_survive_pruning(self):
        from tinyagentos.routes.auth import _FailCounter
        fc = _FailCounter(max_attempts=5, window_seconds=600)
        for _ in range(3):
            fc.record_failure("5.5.5.5")
        # Entry should still be present and not counted as expired
        assert fc.is_limited("5.5.5.5") is False
        assert "5.5.5.5" in fc._log

    def test_reset_removes_entry(self):
        from tinyagentos.routes.auth import _FailCounter
        fc = _FailCounter(max_attempts=5, window_seconds=600)
        fc.record_failure("6.6.6.6")
        fc.reset("6.6.6.6")
        assert "6.6.6.6" not in fc._log


class TestConcurrentHashUpgrade:
    """Hash-upgrade write is protected by a lock — concurrent logins don't lose writes."""

    def test_concurrent_upgrade_does_not_lose_write(self, tmp_path):
        """Two threads racing on a legacy-hash login must both succeed and
        the final stored hash must be valid argon2 (not clobbered back to SHA-256)."""
        import threading
        from tinyagentos.auth import AuthManager, _hash_password_sha256

        mgr = AuthManager(tmp_path)
        # Seed a user with an old SHA-256 hash directly (bypassing set_password)
        import json, time as _time
        legacy_hash = _hash_password_sha256("legacypass", "mysalt")
        user_data = {
            "users": [{
                "id": "u1",
                "username": "legacyuser",
                "full_name": "Legacy",
                "email": "",
                "password_hash": legacy_hash,
                "created_at": _time.time(),
                "last_login_at": None,
                "is_admin": True,
            }],
            "current_user_id": "u1",
        }
        mgr._user_file.parent.mkdir(parents=True, exist_ok=True)
        mgr._user_file.write_text(json.dumps(user_data))

        results = []

        def login():
            ok, _ = mgr.check_password("legacypass", username="legacyuser")
            results.append(ok)

        threads = [threading.Thread(target=login) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All logins must succeed
        assert all(results), f"Some logins failed: {results}"

        # The stored hash must now be argon2 (not corrupted back to SHA-256)
        stored = json.loads(mgr._user_file.read_text())
        final_hash = stored["users"][0]["password_hash"]
        assert final_hash.startswith("$argon2"), f"Hash was not upgraded: {final_hash[:40]}"
