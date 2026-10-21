"""Session-bound CSRF tokens (tsk-rll7hq, CSRF binding 1/3).

A bare random ``csrf_token`` is not tied to ``taos_session``, so a sibling
``{user}.taos.my`` subdomain can plant a ``Domain=.taos.my`` cookie and satisfy
the double-submit check with a header it chose.  A bound token is
``<nonce>.<hmac_sha256(secret, session_id + nonce)>``: only the server can mint
one for a given session, and a token minted for session A does not verify for
session B.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.middleware import csrf

SECRET = b"s" * 32
OTHER_SECRET = b"o" * 32
SESSION_A = "session-token-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SESSION_B = "session-token-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_valid_token_accepted():
    token = csrf.mint_csrf_token(SECRET, SESSION_A)
    assert csrf.verify_bound(SECRET, token, SESSION_A) is True


def test_token_shape_is_nonce_dot_mac():
    token = csrf.mint_csrf_token(SECRET, SESSION_A)
    nonce, mac = token.split(".")
    int(nonce, 16)
    int(mac, 16)
    assert len(mac) == 64  # hex sha256


def test_each_mint_uses_a_fresh_nonce():
    assert csrf.mint_csrf_token(SECRET, SESSION_A) != csrf.mint_csrf_token(SECRET, SESSION_A)


def test_token_for_session_a_rejected_for_session_b():
    token = csrf.mint_csrf_token(SECRET, SESSION_A)
    assert csrf.verify_bound(SECRET, token, SESSION_B) is False


def test_token_rejected_under_a_different_secret():
    token = csrf.mint_csrf_token(SECRET, SESSION_A)
    assert csrf.verify_bound(OTHER_SECRET, token, SESSION_A) is False


def test_tampered_nonce_rejected():
    token = csrf.mint_csrf_token(SECRET, SESSION_A)
    nonce, mac = token.split(".")
    flipped = ("0" if nonce[0] != "0" else "1") + nonce[1:]
    assert csrf.verify_bound(SECRET, f"{flipped}.{mac}", SESSION_A) is False


def test_tampered_mac_rejected():
    token = csrf.mint_csrf_token(SECRET, SESSION_A)
    nonce, mac = token.split(".")
    flipped = mac[:-1] + ("0" if mac[-1] != "0" else "1")
    assert csrf.verify_bound(SECRET, f"{nonce}.{flipped}", SESSION_A) is False


def test_legacy_unbound_token_rejected():
    # What CSRFMiddleware mints today: 64 hex chars, no MAC.
    assert csrf.verify_bound(SECRET, "ab" * 32, SESSION_A) is False


def test_malformed_inputs_rejected_without_raising():
    good = csrf.mint_csrf_token(SECRET, SESSION_A)
    nonce, mac = good.split(".")
    for token in (
        "",
        ".",
        "..",
        f"{nonce}.",
        f".{mac}",
        f"{nonce}.{mac}.extra",
        f"{nonce}{mac}",
        f"zz{nonce[2:]}.{mac}",
        f"{nonce}.{mac}é",
        None,
        123,
        b"bytes",
    ):
        assert csrf.verify_bound(SECRET, token, SESSION_A) is False, token
    for session in ("", None, 5):
        assert csrf.verify_bound(SECRET, good, session) is False, session
    for secret in (b"", None, "not-bytes"):
        assert csrf.verify_bound(secret, good, SESSION_A) is False, secret


# ---------------------------------------------------------------------------
# CSRF binding 2/3 (tsk-34qgmt): the middleware and verify_csrf use the helpers.
# ---------------------------------------------------------------------------


def _csrf_cookie_lines(resp) -> list[str]:
    return [
        line
        for line in resp.headers.get_list("set-cookie")
        if line.partition("=")[0].strip() == "csrf_token"
    ]


def _is_deletion(line: str) -> bool:
    return "max-age=0" in line.lower().replace(" ", "")


def _csrf_cookie_values(resp) -> list[str]:
    """Values of the csrf_token cookies set (deletions skipped)."""
    return [
        line.partition("=")[2].split(";", 1)[0].strip()
        for line in _csrf_cookie_lines(resp)
        if not _is_deletion(line)
    ]


def _two_sessions(app, username):
    auth = app.state.auth
    auth.setup_user(username, "Binding Test", "", "pass1234!")
    user_id = auth.find_user(username)["id"]
    return auth.create_session(user_id=user_id), auth.create_session(user_id=user_id)


def _client(app, cookies):
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies=cookies
    )


@pytest.mark.asyncio
async def test_session_a_token_with_session_b_cookie_forbidden(app):
    session_a, session_b = _two_sessions(app, "bindab")
    token_a = csrf.mint_csrf_token(csrf.get_csrf_secret(app), session_a)
    async with _client(app, {"taos_session": session_b, "csrf_token": token_a}) as c:
        resp = await c.post(
            "/auth/logout", headers={"X-CSRF-Token": token_a}, follow_redirects=False
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_token_bound_to_own_session_accepted(app):
    session_a, _ = _two_sessions(app, "bindok")
    token = csrf.mint_csrf_token(csrf.get_csrf_secret(app), session_a)
    async with _client(app, {"taos_session": session_a, "csrf_token": token}) as c:
        resp = await c.post(
            "/auth/logout", headers={"X-CSRF-Token": token}, follow_redirects=False
        )
    assert resp.status_code == 303
    # A cookie that already verifies is left alone.
    assert _csrf_cookie_values(resp) == []


@pytest.mark.asyncio
async def test_login_sets_csrf_cookie_bound_to_new_session(app):
    app.state.auth.setup_user("bindlogin", "Binding Test", "", "pass1234!")
    # The browser arrives holding the pre-session, unbound token.
    async with _client(app, {"csrf_token": "ab" * 32}) as c:
        resp = await c.post(
            "/auth/login", json={"username": "bindlogin", "password": "pass1234!"}
        )
    assert resp.status_code == 200
    session = resp.cookies.get("taos_session")
    assert session
    issued = _csrf_cookie_values(resp)
    assert len(issued) == 1
    assert csrf.verify_bound(csrf.get_csrf_secret(app), issued[0], session) is True


@pytest.mark.asyncio
async def test_safe_request_reissues_unbound_cookie_bound_to_session(app):
    session, _ = _two_sessions(app, "bindsafe")
    async with _client(app, {"taos_session": session, "csrf_token": "ab" * 32}) as c:
        resp = await c.get("/auth/status")
    assert resp.status_code == 200
    issued = _csrf_cookie_values(resp)
    assert len(issued) == 1
    assert csrf.verify_bound(csrf.get_csrf_secret(app), issued[0], session) is True


@pytest.mark.asyncio
async def test_unsafe_request_with_unbound_cookie_403_then_retry_succeeds(app):
    session, _ = _two_sessions(app, "bindunsafe")
    legacy = "ab" * 32
    async with _client(app, {"taos_session": session, "csrf_token": legacy}) as c:
        resp = await c.post(
            "/auth/logout", headers={"X-CSRF-Token": legacy}, follow_redirects=False
        )
        assert resp.status_code == 403
        assert resp.json() == {"detail": "CSRF token not bound to session"}
        # The 403 carries a fresh bound cookie, so one retry that re-reads the
        # cookie (what the SPA does on every request) goes through.
        issued = _csrf_cookie_values(resp)
        assert len(issued) == 1
        c.cookies.set("csrf_token", issued[0])
        retry = await c.post(
            "/auth/logout", headers={"X-CSRF-Token": issued[0]}, follow_redirects=False
        )
    assert retry.status_code == 303


@pytest.mark.asyncio
async def test_reissue_deletes_host_cookie_before_setting_the_bound_one(app):
    # A replaced cookie keeps its creation time, so a newer parent-Domain
    # cookie planted from a sibling subdomain would stay last in the Cookie
    # header (the one the server reads) and lock the session into 403s.
    # Deleting first makes the re-issued cookie the newest, so it sorts last.
    session, _ = _two_sessions(app, "bindorder")
    async with _client(app, {"taos_session": session, "csrf_token": "ab" * 32}) as c:
        resp = await c.get("/auth/status")
    lines = _csrf_cookie_lines(resp)
    assert len(lines) == 2, lines
    assert _is_deletion(lines[0]) and "domain=" not in lines[0].lower()
    assert not _is_deletion(lines[1])
    issued = _csrf_cookie_values(resp)
    assert csrf.verify_bound(csrf.get_csrf_secret(app), issued[0], session) is True


@pytest.mark.asyncio
async def test_pre_session_request_still_gets_unbound_token(app):
    async with _client(app, {}) as c:
        resp = await c.get("/auth/status")
    issued = _csrf_cookie_values(resp)
    assert len(issued) == 1
    assert "." not in issued[0]
    assert len(issued[0]) == 64


def test_secret_is_stable_and_derived_from_the_persistent_local_token(app, tmp_data_dir):
    from tinyagentos.app import create_app

    first = csrf.get_csrf_secret(app)
    assert isinstance(first, bytes) and len(first) == 32
    assert csrf.get_csrf_secret(app) == first
    # A restart (new app over the same data dir) keeps issued tokens valid.
    assert csrf.get_csrf_secret(create_app(data_dir=tmp_data_dir)) == first
