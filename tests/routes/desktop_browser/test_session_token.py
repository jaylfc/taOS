"""Tests for scoped per-session stream tokens (session_token.py)."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from tinyagentos.shortcuts.tickets import JtiTracker

KEY = b"a" * 32  # exactly 32 bytes, minimal valid key
SHORT_KEY = b"tooshort"


def _fresh_tracker() -> JtiTracker:
    return JtiTracker()


class TestMintValidateRoundtrip:
    def test_returns_correct_session_and_user(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        payload, token = mint_session_token("sess-123", "user-abc", KEY)

        assert payload["session_id"] == "sess-123"
        assert payload["user_id"] == "user-abc"
        assert "exp" in payload
        assert isinstance(token, str)

        result = validate_session_token(token, KEY, tracker=tracker)
        assert result["session_id"] == "sess-123"
        assert result["user_id"] == "user-abc"

    def test_mint_payload_has_no_jti(self):
        """mint returns only session_id, user_id, exp — jti is internal."""
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token

        payload, _ = mint_session_token("s", "u", KEY)
        assert set(payload.keys()) == {"session_id", "user_id", "exp"}

    def test_exp_is_in_future(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token

        payload, _ = mint_session_token("s", "u", KEY, ttl=60)
        assert payload["exp"] > int(time.time())


class TestTamperedToken:
    def test_tampered_token_raises(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token = mint_session_token("sess-1", "user-1", KEY)
        # Flip the last character
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(ValueError):
            validate_session_token(tampered, KEY, tracker=tracker)

    def test_wrong_key_raises(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token = mint_session_token("sess-2", "user-2", KEY)
        wrong_key = b"b" * 32
        with pytest.raises(ValueError):
            validate_session_token(token, wrong_key, tracker=tracker)


class TestExpiredToken:
    def test_expired_token_raises(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token = mint_session_token("sess-3", "user-3", KEY, ttl=60)
        # Advance time past expiry
        future = int(time.time()) + 120
        with patch("tinyagentos.routes.desktop_browser.session_token.time") as mock_time:
            mock_time.time.return_value = future
            with pytest.raises(ValueError, match="expired"):
                validate_session_token(token, KEY, tracker=tracker)

    def test_zero_ttl_expires_immediately(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token = mint_session_token("sess-4", "user-4", KEY, ttl=0)
        future = int(time.time()) + 5
        with patch("tinyagentos.routes.desktop_browser.session_token.time") as mock_time:
            mock_time.time.return_value = future
            with pytest.raises(ValueError, match="expired"):
                validate_session_token(token, KEY, tracker=tracker)


class TestReplay:
    def test_replay_raises_on_second_validate(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token = mint_session_token("sess-5", "user-5", KEY)
        # First validation succeeds
        validate_session_token(token, KEY, tracker=tracker)
        # Second validation with the same token must fail
        with pytest.raises(ValueError, match="replay"):
            validate_session_token(token, KEY, tracker=tracker)

    def test_different_tokens_each_succeed(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token1 = mint_session_token("sess-6", "user-6", KEY)
        _, token2 = mint_session_token("sess-6", "user-6", KEY)
        validate_session_token(token1, KEY, tracker=tracker)
        validate_session_token(token2, KEY, tracker=tracker)  # different jti, must pass


class TestShortKey:
    def test_mint_short_key_raises(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token

        with pytest.raises(ValueError, match="32 bytes"):
            mint_session_token("s", "u", SHORT_KEY)

    def test_validate_short_key_raises(self):
        from tinyagentos.routes.desktop_browser.session_token import mint_session_token, validate_session_token

        tracker = _fresh_tracker()
        _, token = mint_session_token("s", "u", KEY)
        with pytest.raises(ValueError, match="32 bytes"):
            validate_session_token(token, SHORT_KEY, tracker=tracker)
