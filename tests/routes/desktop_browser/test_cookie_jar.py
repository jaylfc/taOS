"""Tests for the httpx cookie-jar adapter wrapping BrowserCookieStore."""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio


TEST_KEY = "a" * 64


@pytest_asyncio.fixture
async def cookie_store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserCookieStore

    s = BrowserCookieStore(tmp_path / "c.sqlite3", key_hex=TEST_KEY)
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestLoadJarForRequest:
    async def test_returns_empty_jar_when_no_cookies(self, cookie_store):
        from tinyagentos.routes.desktop_browser.cookie_jar import load_jar_for_request

        jar = await load_jar_for_request(
            cookie_store, user_id="u1", profile_id="personal", host="github.com",
        )
        assert isinstance(jar, httpx.Cookies)
        assert len(list(jar.jar)) == 0

    async def test_loads_cookies_for_matching_host(self, cookie_store):
        from tinyagentos.routes.desktop_browser.cookie_jar import load_jar_for_request

        await cookie_store.set_cookie(
            user_id="u1", profile_id="personal",
            host="github.com", path="/", name="user_session", value="xyz",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )

        jar = await load_jar_for_request(
            cookie_store, user_id="u1", profile_id="personal", host="github.com",
        )
        cookies = list(jar.jar)
        assert len(cookies) == 1
        assert cookies[0].name == "user_session"
        assert cookies[0].value == "xyz"

    async def test_does_not_load_other_user_cookies(self, cookie_store):
        from tinyagentos.routes.desktop_browser.cookie_jar import load_jar_for_request

        # u1 has a cookie for github.com
        await cookie_store.set_cookie(
            user_id="u1", profile_id="personal",
            host="github.com", path="/", name="user_session", value="from-u1",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )

        # u2 should NOT see u1's cookies
        jar = await load_jar_for_request(
            cookie_store, user_id="u2", profile_id="personal", host="github.com",
        )
        assert len(list(jar.jar)) == 0


@pytest.mark.asyncio
class TestPersistResponseCookies:
    async def test_persists_set_cookie_to_store(self, cookie_store):
        from tinyagentos.routes.desktop_browser.cookie_jar import (
            load_jar_for_request,
            persist_response_cookies,
        )

        # Simulate a response with Set-Cookie
        response_cookies = httpx.Cookies()
        response_cookies.set(
            name="session_id", value="new-token",
            domain="github.com", path="/",
        )

        await persist_response_cookies(
            cookie_store, response_cookies,
            user_id="u1", profile_id="personal",
        )

        # Re-loading the jar should now include the persisted cookie
        jar = await load_jar_for_request(
            cookie_store, user_id="u1", profile_id="personal", host="github.com",
        )
        cookies = list(jar.jar)
        assert any(c.name == "session_id" and c.value == "new-token" for c in cookies)

    async def test_per_user_persistence_isolated(self, cookie_store):
        from tinyagentos.routes.desktop_browser.cookie_jar import (
            load_jar_for_request,
            persist_response_cookies,
        )

        u1_cookies = httpx.Cookies()
        u1_cookies.set(name="sid", value="u1-token", domain="github.com", path="/")

        await persist_response_cookies(
            cookie_store, u1_cookies, user_id="u1", profile_id="personal",
        )

        # u2's jar must not include u1's persisted cookie
        u2_jar = await load_jar_for_request(
            cookie_store, user_id="u2", profile_id="personal", host="github.com",
        )
        assert len(list(u2_jar.jar)) == 0
