"""Tests for browser-cookie key derivation (Argon2id)."""
from __future__ import annotations

import pytest


class TestDeriveCookieKey:
    def test_returns_64_char_hex_string(self):
        from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key

        key = derive_cookie_key(password="hunter2", user_salt=b"u" * 16)

        # SQLCipher needs a 256-bit key, encoded as 64 hex chars
        assert isinstance(key, str)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic_for_same_inputs(self):
        from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key

        a = derive_cookie_key(password="hunter2", user_salt=b"u" * 16)
        b = derive_cookie_key(password="hunter2", user_salt=b"u" * 16)
        assert a == b

    def test_different_passwords_produce_different_keys(self):
        from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key

        a = derive_cookie_key(password="hunter2", user_salt=b"u" * 16)
        b = derive_cookie_key(password="other", user_salt=b"u" * 16)
        assert a != b

    def test_different_salts_produce_different_keys(self):
        from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key

        a = derive_cookie_key(password="hunter2", user_salt=b"a" * 16)
        b = derive_cookie_key(password="hunter2", user_salt=b"b" * 16)
        assert a != b

    def test_rejects_short_salt(self):
        from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key

        with pytest.raises(ValueError, match="salt"):
            derive_cookie_key(password="hunter2", user_salt=b"short")

    def test_rejects_empty_password(self):
        from tinyagentos.routes.desktop_browser.crypto import derive_cookie_key

        with pytest.raises(ValueError, match="password"):
            derive_cookie_key(password="", user_salt=b"u" * 16)
