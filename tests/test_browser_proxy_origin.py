"""Tests for tinyagentos.browser_proxy_origin pure functions."""
from __future__ import annotations

import os
import sys
import time

import pytest

# Ensure the project root is on sys.path so the editable-installed
# tinyagentos package is importable when pytest bypasses the .pth hook.
_PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


class _FakeState:
    """Minimal stand-in for app.state with configurable attributes."""

    def __init__(self, attrs: dict | None = None):
        if attrs:
            for k, v in attrs.items():
                setattr(self, k, v)


class _FakeAuthMgr:
    """Minimal auth manager that returns users by id."""

    def __init__(self, users: dict | None = None):
        self._users = users or {}

    def get_user_by_id(self, user_id: str):
        return self._users.get(user_id)


# ---------------------------------------------------------------------------
# _is_safe_next
# ---------------------------------------------------------------------------


class TestIsSafeNext:
    """_is_safe_next validates the redirect target in the redeem flow."""

    def test_valid_proxy_path_is_safe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("/api/desktop/browser/proxy") is True

    def test_valid_proxy_path_with_query_is_safe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("/api/desktop/browser/proxy?url=https%3A%2F%2Fexample.com") is True

    def test_absolute_url_is_unsafe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("https://evil.com/steal") is False

    def test_scheme_relative_is_unsafe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("//evil.com/steal") is False

    def test_root_path_is_unsafe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("/") is False

    def test_other_path_is_unsafe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("/__taos/redeem") is False

    def test_empty_string_is_unsafe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("") is False

    def test_double_slash_is_unsafe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        assert _is_safe_next("/api/desktop/browser/proxy/extra") is False

    def test_path_with_fragment_is_safe(self):
        from tinyagentos.browser_proxy_origin import _is_safe_next

        # Fragment is stripped by urlsplit; path still matches proxy endpoint.
        # The function guards against off-origin redirects, not fragments.
        assert _is_safe_next("/api/desktop/browser/proxy#fragment") is True


# ---------------------------------------------------------------------------
# _session_store
# ---------------------------------------------------------------------------


class TestSessionStore:
    """_session_store lazily initializes and returns the session dict."""

    def test_creates_store_when_missing(self):
        from tinyagentos.browser_proxy_origin import _session_store

        state = _FakeState()
        store = _session_store(state)

        assert store is not None
        assert isinstance(store, dict)
        assert store is state.browser_proxy_sessions

    def test_returns_existing_store(self):
        from tinyagentos.browser_proxy_origin import _session_store

        existing: dict = {}
        state = _FakeState({"browser_proxy_sessions": existing})
        store = _session_store(state)

        assert store is existing


# ---------------------------------------------------------------------------
# _new_browser_session
# ---------------------------------------------------------------------------


class TestNewBrowserSession:
    """_new_browser_session creates a session and returns its id."""

    def test_creates_session_with_user_and_expiry(self):
        from tinyagentos.browser_proxy_origin import _new_browser_session

        state = _FakeState()
        session_id = _new_browser_session(state, "user-42")

        assert isinstance(session_id, str)
        assert len(session_id) > 0

        store = state.browser_proxy_sessions
        assert session_id in store
        assert store[session_id]["user_id"] == "user-42"
        assert store[session_id]["expires_at"] > time.monotonic()

    def test_each_call_returns_unique_id(self):
        from tinyagentos.browser_proxy_origin import _new_browser_session

        state = _FakeState()
        id1 = _new_browser_session(state, "user-1")
        id2 = _new_browser_session(state, "user-1")

        assert id1 != id2
        assert len(state.browser_proxy_sessions) == 2


# ---------------------------------------------------------------------------
# _resolve_browser_session
# ---------------------------------------------------------------------------


class TestResolveBrowserSession:
    """_resolve_browser_session validates and resolves a session id."""

    def test_valid_session_returns_user_id(self):
        from tinyagentos.browser_proxy_origin import (
            _new_browser_session,
            _resolve_browser_session,
        )

        state = _FakeState()
        session_id = _new_browser_session(state, "user-99")

        result = _resolve_browser_session(state, session_id)

        assert result == "user-99"

    def test_unknown_session_returns_none(self):
        from tinyagentos.browser_proxy_origin import _resolve_browser_session

        state = _FakeState()

        assert _resolve_browser_session(state, "nonexistent-id") is None

    def test_expired_session_returns_none_and_removes_entry(self):
        from tinyagentos.browser_proxy_origin import (
            _new_browser_session,
            _resolve_browser_session,
        )

        state = _FakeState()
        session_id = _new_browser_session(state, "user-ttl")
        entry = state.browser_proxy_sessions[session_id]
        entry["expires_at"] = time.monotonic() - 1

        result = _resolve_browser_session(state, session_id)

        assert result is None
        assert session_id not in state.browser_proxy_sessions

    def test_valid_session_refreshes_expiry(self):
        from tinyagentos.browser_proxy_origin import (
            _new_browser_session,
            _resolve_browser_session,
        )

        state = _FakeState()
        session_id = _new_browser_session(state, "user-refresh")
        original_expiry = state.browser_proxy_sessions[session_id]["expires_at"]

        time.sleep(0.01)
        _resolve_browser_session(state, session_id)

        assert state.browser_proxy_sessions[session_id]["expires_at"] > original_expiry


# ---------------------------------------------------------------------------
# _SharedState
# ---------------------------------------------------------------------------


class TestSharedState:
    """_SharedState delegates allowlisted attrs and isolates local ones."""

    def test_allowlisted_attribute_delegates_to_shared(self):
        from tinyagentos.browser_proxy_origin import _SharedState

        shared = _FakeState({"auth": _FakeAuthMgr(), "main_port": 6969})
        proxy = _SharedState(shared)

        assert proxy.auth is shared.auth
        assert proxy.main_port == 6969

    def test_non_allowlisted_attribute_raises(self):
        from tinyagentos.browser_proxy_origin import _SharedState

        shared = _FakeState({"auth": _FakeAuthMgr()})
        proxy = _SharedState(shared)

        with pytest.raises(AttributeError, match="not in the proxy-origin allowlist"):
            proxy.secrets

    def test_local_setattr_does_not_leak_to_shared(self):
        from tinyagentos.browser_proxy_origin import _SharedState

        shared = _FakeState({"auth": _FakeAuthMgr()})
        proxy = _SharedState(shared)

        proxy.browser_proxy_sessions = {"abc": 123}

        assert proxy.browser_proxy_sessions == {"abc": 123}
        assert not hasattr(shared, "browser_proxy_sessions")

    def test_local_getattr_takes_priority_over_shared(self):
        from tinyagentos.browser_proxy_origin import _SharedState

        shared = _FakeState({"auth": _FakeAuthMgr()})
        proxy = _SharedState(shared)

        proxy.auth = "local-override"

        assert proxy.auth == "local-override"

    def test_init_rejects_extra_kwargs(self):
        from tinyagentos.browser_proxy_origin import _SharedState

        shared = _FakeState()

        with pytest.raises(TypeError):
            _SharedState(shared, bad_kwarg=1)
