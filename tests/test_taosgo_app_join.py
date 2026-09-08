"""Tests for POST /api/taosgo/app-join.

app-join (PR 130) is currently password-only and paused at go-live.  The handler
used to carry two dead branches that the global auth gate (``AuthMiddleware``)
masks:

* a "password-only bypass" that granted the primary user to a caller with no
  session and no Bearer when the primary user had no PIN, and
* an app-password Bearer branch whose parameter was never wired with
  ``Depends(HTTPBearer(...))`` so the value was always ``None``.

Both are removed.  The Bearer path is now wired with
``Depends(HTTPBearer(auto_error=False))`` and an unauthenticated caller is
rejected with 401 *by the handler itself* (defence in depth, so a future
allowlist drift cannot arm the route for anonymous callers).

CSRF stays ON: this module has no ``csrf_bypass`` marker and the real
``verify_csrf`` is asserted by ``test_csrf_dependency_is_real``.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tinyagentos.app import create_app
from tinyagentos.middleware.csrf import _COOKIE_NAME

USERNAME = "admin"
PASSWORD = "testpass123"
APP_JOIN_PATH = "/api/taosgo/app-join"


@pytest.fixture
def app(tmp_data_dir):
    """App backed by the shared conftest ``tmp_data_dir``.

    A real account is set up here (no PIN) so the handler has a primary user to
    resolve, exactly the PR-130 window this endpoint occupies.  The conftest
    ``client`` fixture is intentionally NOT used: it injects a session cookie,
    and app-join's auth is the very thing under test.
    """
    built = create_app(data_dir=tmp_data_dir)
    built.state.auth.setup_user(USERNAME, "Test Admin", "", PASSWORD)
    return built


def _csrf_token(client):
    """Return the ``csrf_token`` cookie value on ``client``'s jar (or None)."""
    return client.cookies.get(_COOKIE_NAME)


def _session_token(app):
    """Mint a real long-lived session for the configured admin."""
    uid = app.state.auth.find_user(USERNAME)["id"]
    return app.state.auth.create_session(user_id=uid, long_lived=True)


@pytest.fixture
def handler_app(app, monkeypatch):
    """The app with the auth gate allowlisted for app-join only.

    This isolates the handler from ``AuthMiddleware`` (whose 401 would shadow
    the one we want to assert) WITHOUT touching the real ``EXEMPT_PATHS``
    allowlist or stubbing CSRF.  CSRF is left real: any request that reaches the
    handler still has to satisfy ``verify_csrf``.
    """
    from tinyagentos import auth_middleware

    original = auth_middleware._is_exempt

    def _exempt(method, path):
        if path == APP_JOIN_PATH:
            return True
        return original(method, path)

    monkeypatch.setattr(auth_middleware, "_is_exempt", _exempt)
    return app


class TestTaosgoAppJoin:
    """Coverage for the app-join endpoint after the dead-branch removal."""

    def test_no_credentials_rejected_by_handler(self, handler_app):
        """Red-on-dev / green-after-fix.

        A session-less, CSRF-valid POST with no Authorization must be rejected
        by the handler itself.  On current dev the dead password-only bypass
        grants the primary user for the no-PIN account and returns 200 with a
        placeholder preauth_key, so this assertion FAILS against dev.  After the
        bypass is removed the handler answers 401 directly.
        """
        client = TestClient(handler_app)
        client.get("/")  # CSRFMiddleware issues a csrf_token cookie
        csrf = _csrf_token(client)

        resp = client.post(
            APP_JOIN_PATH,
            headers={"X-CSRF-Token": csrf} if csrf else {},
        )

        assert resp.status_code == 401, resp.text
        body = resp.json()
        # A handler-raised 401 carries ``detail``; the auth gate's 401 carries
        # ``error``.  Asserting ``detail`` proves we hit the handler, not the gate.
        assert "detail" in body
        assert "error" not in body

    def test_signed_in_with_csrf_returns_key(self, app):
        """A real session caller with a valid CSRF double-submit still gets a key."""
        client = TestClient(app)
        client.get("/")  # obtain csrf_token cookie
        client.cookies.set("taos_session", _session_token(app))
        csrf = _csrf_token(client)

        resp = client.post(
            APP_JOIN_PATH, headers={"X-CSRF-Token": csrf} if csrf else {}
        )
        assert resp.status_code == 200, resp.text
        assert "preauth_key" in resp.json()

    def test_signed_in_without_csrf_rejected(self, app):
        """A session caller that drops the CSRF header is rejected (403)."""
        client = TestClient(app)
        client.cookies.set("taos_session", _session_token(app))
        resp = client.post(APP_JOIN_PATH)
        assert resp.status_code == 403

    def test_bearer_local_token_returns_key(self, app):
        """A valid local-token Bearer is accepted and mints a key."""
        client = TestClient(app)
        token = app.state.auth.get_local_token()
        resp = client.post(
            APP_JOIN_PATH, headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        assert "preauth_key" in resp.json()

    def test_bearer_invalid_token_rejected_by_handler(self, handler_app):
        """A Bearer that fails local-token validation is rejected by the handler
        (not by the gate, which is allowlisted here)."""
        client = TestClient(handler_app)
        resp = client.post(
            APP_JOIN_PATH,
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401, resp.text


def test_csrf_dependency_is_real():
    """Guard: this module runs against the real verify_csrf, never a stub."""
    from tinyagentos.middleware import csrf

    assert csrf.verify_csrf.__module__ == "tinyagentos.middleware.csrf"
    assert csrf.verify_csrf.__name__ == "verify_csrf"
