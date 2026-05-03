"""Tests for BrowserCookieStore — SQLCipher encryption + multi-user isolation."""
from __future__ import annotations

import pytest
import pytest_asyncio


# A 64-char hex string = 256-bit key. In production this comes from
# derive_cookie_key. Tests use a fixed value so they are deterministic.
TEST_KEY = "a" * 64
WRONG_KEY = "b" * 64


@pytest_asyncio.fixture
async def cookie_store(tmp_path):
    from tinyagentos.routes.desktop_browser.store import BrowserCookieStore

    s = BrowserCookieStore(tmp_path / "browser_cookies.sqlite3", key_hex=TEST_KEY)
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
class TestCookieStoreEncryption:
    async def test_db_is_not_readable_with_wrong_key(self, tmp_path):
        from tinyagentos.routes.desktop_browser.store import BrowserCookieStore

        # Create + close
        s = BrowserCookieStore(tmp_path / "c.sqlite3", key_hex=TEST_KEY)
        await s.init()
        await s.set_cookie(
            user_id="u1", profile_id="p1",
            host="x.test", path="/", name="sid", value="abc",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )
        await s.close()

        # Reopen with wrong key — read must fail (init may also fail
        # depending on SQLCipher version; either way, the data must
        # not be retrievable with the wrong key)
        s2 = BrowserCookieStore(tmp_path / "c.sqlite3", key_hex=WRONG_KEY)
        with pytest.raises(Exception):
            try:
                await s2.init()
            except Exception:
                # init() rejected the wrong key — that's an acceptable failure mode.
                # Re-raise so the outer `pytest.raises` records it.
                raise
            # If init() succeeded silently, the read MUST fail
            await s2.get_cookies(user_id="u1", profile_id="p1", host="x.test")

    async def test_rejects_non_hex_key(self, tmp_path):
        from tinyagentos.routes.desktop_browser.store import BrowserCookieStore

        # 64 chars but not valid hex
        with pytest.raises(ValueError, match="hex"):
            BrowserCookieStore(tmp_path / "c.sqlite3", key_hex="z" * 64)

    async def test_rejects_short_key(self, tmp_path):
        from tinyagentos.routes.desktop_browser.store import BrowserCookieStore

        with pytest.raises(ValueError, match="64 hex chars"):
            BrowserCookieStore(tmp_path / "c.sqlite3", key_hex="abc")

    async def test_raw_file_is_not_plaintext(self, tmp_path):
        from tinyagentos.routes.desktop_browser.store import BrowserCookieStore

        s = BrowserCookieStore(tmp_path / "c.sqlite3", key_hex=TEST_KEY)
        await s.init()
        await s.set_cookie(
            user_id="u1", profile_id="p1",
            host="x.test", path="/", name="sid", value="MY-SECRET-VALUE",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )
        await s.close()

        raw = (tmp_path / "c.sqlite3").read_bytes()
        # The plain cookie value must not appear in the on-disk file
        assert b"MY-SECRET-VALUE" not in raw


@pytest.mark.asyncio
class TestCookieStoreCRUD:
    async def test_set_and_get_single_cookie(self, cookie_store):
        await cookie_store.set_cookie(
            user_id="u1", profile_id="p1",
            host="github.com", path="/", name="user_session", value="xyz",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )
        result = await cookie_store.get_cookies(
            user_id="u1", profile_id="p1", host="github.com",
        )
        assert len(result) == 1
        assert result[0]["name"] == "user_session"
        assert result[0]["value"] == "xyz"

    async def test_user_isolation(self, cookie_store):
        # u1 sets a github.com cookie; u2 must not see it
        await cookie_store.set_cookie(
            user_id="u1", profile_id="p1",
            host="github.com", path="/", name="user_session", value="from-u1",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )
        u2_cookies = await cookie_store.get_cookies(
            user_id="u2", profile_id="p1", host="github.com",
        )
        assert u2_cookies == []

    async def test_profile_isolation(self, cookie_store):
        # Same user, two profiles, must not cross
        await cookie_store.set_cookie(
            user_id="u1", profile_id="personal",
            host="github.com", path="/", name="user_session", value="personal-token",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )
        await cookie_store.set_cookie(
            user_id="u1", profile_id="work",
            host="github.com", path="/", name="user_session", value="work-token",
            expires_at=None, http_only=True, secure=True, same_site="lax",
        )

        personal = await cookie_store.get_cookies(
            user_id="u1", profile_id="personal", host="github.com",
        )
        work = await cookie_store.get_cookies(
            user_id="u1", profile_id="work", host="github.com",
        )
        assert personal[0]["value"] == "personal-token"
        assert work[0]["value"] == "work-token"

    async def test_set_cookie_requires_user_id(self, cookie_store):
        with pytest.raises(ValueError, match="user_id"):
            await cookie_store.set_cookie(
                user_id="", profile_id="p1",
                host="x.test", path="/", name="n", value="v",
                expires_at=None, http_only=True, secure=True, same_site=None,
            )

    async def test_get_cookies_requires_user_id(self, cookie_store):
        with pytest.raises(ValueError, match="user_id"):
            await cookie_store.get_cookies(user_id="", profile_id="p1", host="x.test")
