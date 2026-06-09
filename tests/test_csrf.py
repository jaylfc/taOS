"""Tests for CSRF double-submit cookie protection (#648)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


class TestCSRFMiddleware:
    """CSRFMiddleware sets a csrf_token cookie on every response."""

    @pytest.mark.asyncio
    async def test_csrf_cookie_set_on_response(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        # The middleware must have set a csrf_token cookie.
        assert "csrf_token" in resp.cookies or "csrf_token" in resp.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_csrf_cookie_not_httponly(self, client):
        """JavaScript must be able to read the csrf_token cookie (not HttpOnly)."""
        resp = await client.get("/api/health")
        set_cookie = resp.headers.get("set-cookie", "")
        if "csrf_token" in set_cookie:
            # The cookie header must NOT contain HttpOnly for the csrf_token.
            parts = [p.strip().lower() for p in set_cookie.split(";")]
            assert "httponly" not in parts


class TestVerifyCSRF:
    """verify_csrf dependency enforces double-submit on authenticated mutating routes."""

    @pytest.mark.asyncio
    async def test_logout_without_csrf_token_forbidden(self, app):
        """Authenticated logout without X-CSRF-Token header returns 403."""
        app.state.auth.setup_user("admin2", "Admin", "", "pass1234!")
        record = app.state.auth.find_user("admin2")
        token = app.state.auth.create_session(user_id=record["id"], long_lived=False)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": token, "csrf_token": "abc123"},
        ) as c:
            # Has session cookie AND csrf_token cookie but NO X-CSRF-Token header.
            resp = await c.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_logout_with_csrf_token_succeeds(self, app):
        """Authenticated logout with matching X-CSRF-Token header succeeds."""
        app.state.auth.setup_user("admin3", "Admin", "", "pass1234!")
        record = app.state.auth.find_user("admin3")
        token = app.state.auth.create_session(user_id=record["id"], long_lived=False)
        csrf_val = "test-csrf-value-xyz"

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": token, "csrf_token": csrf_val},
        ) as c:
            resp = await c.post(
                "/auth/logout",
                headers={"X-CSRF-Token": csrf_val},
                follow_redirects=False,
            )
        assert resp.status_code == 303

    @pytest.mark.asyncio
    async def test_logout_with_mismatched_csrf_token_forbidden(self, app):
        """Mismatched X-CSRF-Token header returns 403."""
        app.state.auth.setup_user("admin4", "Admin", "", "pass1234!")
        record = app.state.auth.find_user("admin4")
        token = app.state.auth.create_session(user_id=record["id"], long_lived=False)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": token, "csrf_token": "real-token"},
        ) as c:
            resp = await c.post(
                "/auth/logout",
                headers={"X-CSRF-Token": "wrong-token"},
                follow_redirects=False,
            )
        assert resp.status_code == 403
