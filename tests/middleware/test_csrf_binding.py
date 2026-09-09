"""RED-FIRST: CSRF token must be bound to the session id.

A token minted for session A must be rejected when presented alongside a
session B cookie.  The current implementation generates a bare random value
with no session binding, so both sessions accept the same token interchangeably.
"""
from __future__ import annotations

import hmac
import hashlib
import secrets

import pytest
from starlette.requests import HTTPConnection, Request

from tinyagentos.middleware.csrf import verify_csrf


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIGNING_KEY = secrets.token_bytes(32)


def _mint(session_id: str) -> str:
    """Replicate the middleware's token-minting algorithm for test setup."""
    msg = session_id.encode() if session_id else b""
    return hmac.new(_SIGNING_KEY, msg, hashlib.sha256).hexdigest()


def _make_app_with_key():
    """Return a minimal app object with a fixed signing key on state."""
    from types import SimpleNamespace

    fake_app = SimpleNamespace()
    fake_app.state = SimpleNamespace()
    fake_app.state.browser_session_signing_key = _SIGNING_KEY
    return fake_app


def _make_request(
    *,
    taos_session: str = "",
    csrf_token: str = "",
    csrf_header: str = "",
    method: str = "POST",
    path: str = "/api/taosgo/app-join",
    authorization: str = "",
    app=None,
) -> Request:
    """Build a Starlette Request suitable for verify_csrf."""
    headers: list[tuple[bytes, bytes]] = []
    if csrf_header:
        headers.append(("x-csrf-token".encode(), csrf_header.encode()))
    if authorization:
        headers.append(("authorization".encode(), authorization.encode()))

    cookie_parts = []
    if taos_session:
        cookie_parts.append(f"taos_session={taos_session}")
    if csrf_token:
        cookie_parts.append(f"csrf_token={csrf_token}")
    if cookie_parts:
        headers.append(("cookie".encode(), "; ".join(cookie_parts).encode()))

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": headers,
        "query_string": b"",
        "app": app,
    }
    return Request(scope)


def _make_ws_conn(app) -> HTTPConnection:
    """Build a bare HTTPConnection with a websocket scope."""
    scope = {"type": "websocket", "headers": [], "app": app}
    return HTTPConnection(scope)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCsrfTokenBinding:
    """Token binding to session id."""

    def test_a_token_minted_for_session_a_is_rejected_on_session_b(self):
        """A csrf_token valid for session A must 403 when session B is active."""
        sess_a = "session-token-a-0000"
        sess_b = "session-token-b-1111"

        token_for_a = _mint(sess_a)
        app = _make_app_with_key()

        request = _make_request(
            taos_session=sess_b,
            csrf_token=token_for_a,
            csrf_header=token_for_a,
            app=app,
        )

        with pytest.raises(Exception) as exc_info:
            verify_csrf(request)

        assert getattr(exc_info.value, "status_code", None) == 403, (
            "expected 403 when csrf_token was minted for a different session"
        )

    def test_token_verification_depends_on_the_session_id(self):
        """verify_csrf must reject a token when the session id changes.

        The current implementation compares cookie vs header blindly, so a
        token minted for session A passes verification under session B.
        """
        sess_a = "session-token-a-0000"
        sess_b = "session-token-b-1111"

        token = _mint(sess_a)
        app = _make_app_with_key()

        request = _make_request(
            taos_session=sess_b,
            csrf_token=token,
            csrf_header=token,
            app=app,
        )

        with pytest.raises(Exception) as exc_info:
            verify_csrf(request)

        assert getattr(exc_info.value, "status_code", None) == 403, (
            "expected 403 when the same token is presented under a different session"
        )

    def test_correct_session_token_accepted(self):
        """A csrf_token minted for the active session must pass."""
        sess_a = "session-token-a-0000"
        token_for_a = _mint(sess_a)
        app = _make_app_with_key()

        request = _make_request(
            taos_session=sess_a,
            csrf_token=token_for_a,
            csrf_header=token_for_a,
            app=app,
        )

        assert verify_csrf(request) is None

    def test_bearer_exemption_unchanged(self):
        """Bearer-authenticated requests must still be exempt."""
        app = _make_app_with_key()
        request = _make_request(
            method="POST",
            path="/api/taosgo/app-join",
            authorization="Bearer tok-xyz",
            app=app,
        )
        assert verify_csrf(request) is None

    def test_websocket_exemption_unchanged(self):
        """WebSocket scopes must still be exempt."""
        app = _make_app_with_key()
        ws_conn = _make_ws_conn(app)
        assert verify_csrf(ws_conn) is None
