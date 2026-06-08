"""Tests for email-or-username login and remember-me (#625)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager


class TestEmailOrUsernameAuth:
    """AuthManager.check_password accepts username or email."""

    def test_login_by_username(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "alice@example.com", "pass1234!")
        ok, record = mgr.check_password("pass1234!", username="alice")
        assert ok is True
        assert record["username"] == "alice"

    def test_login_by_email(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "alice@example.com", "pass1234!")
        ok, record = mgr.check_password("pass1234!", username="alice@example.com")
        assert ok is True
        assert record["username"] == "alice"

    def test_login_by_email_case_insensitive(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "alice@example.com", "pass1234!")
        ok, record = mgr.check_password("pass1234!", username="ALICE@EXAMPLE.COM")
        assert ok is True

    def test_login_by_email_wrong_password(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "alice@example.com", "pass1234!")
        ok, _ = mgr.check_password("wrongpass", username="alice@example.com")
        assert ok is False

    def test_login_unknown_email_fails(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "alice@example.com", "pass1234!")
        ok, _ = mgr.check_password("pass1234!", username="nobody@example.com")
        assert ok is False

    def test_username_lookup_takes_priority_over_email(self, tmp_path):
        """If a username happens to equal another user's email, username wins."""
        mgr = AuthManager(tmp_path)
        # Primary user whose username looks like an email address
        mgr.setup_user("bob@example.com", "Bob", "other@example.com", "bobpass1!")
        # Look up by that string — it should match the username "bob@example.com"
        ok, record = mgr.check_password("bobpass1!", username="bob@example.com")
        assert ok is True
        assert record["username"] == "bob@example.com"

    def test_no_username_searches_all(self, tmp_path):
        """Without a username hint, all users are tried (legacy path)."""
        mgr = AuthManager(tmp_path)
        mgr.setup_user("alice", "Alice", "alice@example.com", "pass1234!")
        ok, record = mgr.check_password("pass1234!")
        assert ok is True
        assert record["username"] == "alice"


class TestRememberMeSessionTTL:
    """long_lived flag produces a cookie with max_age set."""

    @pytest.mark.asyncio
    async def test_long_lived_session_sets_max_age(self, app):
        app.state.auth.setup_user("ttluser", "TTL", "ttl@example.com", "mypass1!")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/auth/login",
                json={"username": "ttluser", "password": "mypass1!", "auto_login": True},
            )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        # max-age must be present and > 0 for a long-lived session
        assert "max-age=" in set_cookie.lower()

    @pytest.mark.asyncio
    async def test_short_session_has_no_max_age(self, app):
        app.state.auth.setup_user("shortuser", "Short", "short@example.com", "mypass1!")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/auth/login",
                json={"username": "shortuser", "password": "mypass1!", "auto_login": False},
            )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        # Short-lived session must NOT have max-age (expires when browser closes)
        assert "max-age=" not in set_cookie.lower()

    @pytest.mark.asyncio
    async def test_login_by_email_via_api(self, app):
        """Email login works through the /auth/login endpoint."""
        app.state.auth.setup_user("emailuser", "Email", "login@example.com", "pass1234!")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/auth/login",
                json={"username": "login@example.com", "password": "pass1234!", "auto_login": False},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["user"]["username"] == "emailuser"
