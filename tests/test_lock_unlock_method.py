"""Settings -> Lock screen: the per-user unlock method, and /auth/swipe-unlock.

HOSTILE FIRST. ``swipe`` is a credential-free way into the device, so the
tests that matter are the ones that measure what gets REFUSED: every one of
the route's conditions is broken on its own, with the others held true, and
must produce a 403 and no session. The happy paths come after, and exist so a
route that refused everything could not pass this file.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager, effective_unlock_method

PASSWORD = "correct horse battery staple"
UA = "Mozilla/5.0 (Linux; taOS kiosk) Chromium/131"
CSRF = "t" * 64


@pytest.fixture(autouse=True)
def _clean_limiters():
    from tinyagentos.routes import auth as auth_routes
    from tinyagentos.routes import lock_settings

    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    lock_settings._reauth_limiter = type(lock_settings._reauth_limiter)()
    yield
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    lock_settings._reauth_limiter = type(lock_settings._reauth_limiter)()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from tinyagentos.app import create_app

    monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
    application = create_app(data_dir=tmp_path)
    mgr = AuthManager(tmp_path)
    mgr.setup_user("owner", "Device Owner", "", PASSWORD)
    application.state.auth = mgr
    return application


def _client(app, host, console_header=True):
    """A client at *host*. By default it sends X-taOS-Console, as the lock
    screen does, so a refusal test below measures the one condition it names
    and not the simple-request gate (#3204) -- which has its own tests, driven
    through ``bare_console``."""
    headers = {"User-Agent": UA}
    if console_header:
        headers["X-taOS-Console"] = "1"
    transport = ASGITransport(app=app, client=(host, 51234))
    return AsyncClient(
        transport=transport, base_url="http://localhost:6969", headers=headers,
    )


@pytest_asyncio.fixture()
async def console(app):
    async with _client(app, "127.0.0.1") as c:
        yield c


@pytest_asyncio.fixture()
async def bare_console(app):
    """Loopback with no X-taOS-Console: what a web page in a browser on the
    device can send with a form or a no-cors fetch."""
    async with _client(app, "127.0.0.1", console_header=False) as c:
        yield c


@pytest_asyncio.fixture()
async def lan(app):
    async with _client(app, "192.168.1.10") as c:
        yield c


def _mgr(app) -> AuthManager:
    return app.state.auth


def _force_method(app, username: str, method: str) -> None:
    """Write the stored value directly, bypassing the API's own checks, so a
    refusal test measures the ROUTE's gate and not the setter's."""
    mgr = _mgr(app)
    data = mgr._read_users()
    for u in data["users"]:
        if u["username"] == username:
            u["unlock_method"] = method
    mgr._write_users(data)


def _add_second_user(app, name="bob", password="bobs password 123"):
    mgr = _mgr(app)
    code = mgr.add_user_invite(name, "owner")
    mgr.complete_invite(name, code, "Bob", "", password)
    return password


def _signed_in(client, app, username: str):
    """Give *client* a real session for *username*, bound to its UA, + CSRF."""
    mgr = _mgr(app)
    uid = mgr.find_user(username)["id"]
    token = mgr.create_session(user_id=uid, user_agent=UA)
    client.cookies.set("taos_session", token)
    client.cookies.set("csrf_token", CSRF)
    return token


async def _put(client, **body):
    return await client.put(
        "/api/settings/lock", json=body, headers={"X-CSRF-Token": CSRF},
    )


# --------------------------------------------------------------------------- #
#  /auth/swipe-unlock -- each condition broken alone                          #
# --------------------------------------------------------------------------- #

class TestSwipeUnlockRefuses:

    @pytest.mark.asyncio
    async def test_from_the_lan_is_refused(self, app, lan):
        _force_method(app, "owner", "swipe")
        r = await lan.post("/auth/swipe-unlock")
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    @pytest.mark.parametrize("header", [
        "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto",
        "X-Real-IP", "Forwarded", "Via",
    ])
    async def test_loopback_with_a_forwarding_header_is_refused(self, app, console, header):
        """Behind a reverse proxy every LAN request is loopback."""
        _force_method(app, "owner", "swipe")
        r = await console.post("/auth/swipe-unlock", headers={header: "192.168.1.10"})
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_lan_cannot_spoof_loopback(self, app, lan):
        _force_method(app, "owner", "swipe")
        r = await lan.post("/auth/swipe-unlock",
                           headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"})
        assert r.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["pin", "password"])
    async def test_an_owner_who_did_not_choose_swipe_is_refused(self, app, console, method):
        _mgr(app).set_pin("owner", "4913")
        _force_method(app, "owner", method)
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_default_method_is_never_swipe(self, app, console):
        """No stored method at all: an existing user gets pin/password, not swipe."""
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_multi_user_install_is_refused(self, app, console):
        _force_method(app, "owner", "swipe")
        _add_second_user(app)
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_a_pending_invite_makes_it_not_single_user(self, app, console):
        _force_method(app, "owner", "swipe")
        _mgr(app).add_user_invite("carol", "owner")
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 403

    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "http://evil.example"},
    ])
    async def test_a_cross_origin_page_is_refused(self, app, console, headers):
        _force_method(app, "owner", "swipe")
        r = await console.post("/auth/swipe-unlock", headers=headers)
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_refusals_are_throttled_like_pin_login(self, app, console):
        _mgr(app).set_pin("owner", "4913")
        for _ in range(5):
            assert (await console.post("/auth/swipe-unlock")).status_code == 403
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) > 0

    @pytest.mark.asyncio
    async def test_the_pin_throttle_also_holds_the_swipe(self, app, console):
        """One limiter, one key: an engaged lockout blocks the swipe too."""
        from tinyagentos.routes import auth as auth_routes

        _force_method(app, "owner", "swipe")
        uid = _mgr(app).find_user("owner")["id"]
        for _ in range(5):
            auth_routes._pin_limiter.record_failure(uid)
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 429
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_lan_refusals_cannot_lock_the_owner_out(self, app, lan, console):
        """The throttle is touched only for console callers."""
        _force_method(app, "owner", "swipe")
        for _ in range(20):
            await lan.post("/auth/swipe-unlock")
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 200


class TestSwipeUnlockRefusesSimpleRequests:
    """#3204 (M5): like every /auth/lock-* POST, swipe-unlock is pre-auth, so
    loopback alone cannot be the gate -- a page open in a browser on the device
    is loopback too. A simple request (a form, or a no-cors fetch, which can
    set neither a custom header nor Content-Type: application/json) is refused
    with every other condition true; with the header, the route's own rules
    decide."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", [
        {},
        {"content": b"{}", "headers": {"Content-Type": "text/plain"}},
        {"content": b"{}", "headers": {"Content-Type": "text/plain;charset=UTF-8"}},
        {"data": {"x": "1"}},
        {"files": {"f": ("a.txt", b"x")}},
        {"content": b"{}", "headers": {"X-taOS-Console": "  "}},
    ], ids=["no-body", "text-plain", "text-plain-charset", "form",
            "multipart", "blank-header"])
    async def test_a_simple_request_is_refused(self, app, bare_console, kwargs):
        _force_method(app, "owner", "swipe")
        r = await bare_console.post("/auth/swipe-unlock", **kwargs)
        assert r.status_code == 403, r.text
        assert "taos_session" not in r.cookies
        assert r.json()["error"] == "console header required"

    @pytest.mark.asyncio
    async def test_simple_refusals_do_not_touch_the_throttle(self, app, bare_console, console):
        """Sent while the method is NOT swipe, where a request that got past the
        gate would count as a failed attempt: a page in a local browser must not
        be able to push the owner into a lockout."""
        _force_method(app, "owner", "password")
        for _ in range(20):
            await bare_console.post("/auth/swipe-unlock", content=b"{}",
                                    headers={"Content-Type": "text/plain"})
        _force_method(app, "owner", "swipe")
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 200
        assert r.cookies.get("taos_session")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", [
        {"headers": {"X-taOS-Console": "1"}},
        {"json": {}},
    ], ids=["console-header", "application-json"])
    async def test_a_non_simple_request_reaches_the_route_rules(self, app, bare_console, kwargs):
        # swipe chosen: opens.
        _force_method(app, "owner", "swipe")
        r = await bare_console.post("/auth/swipe-unlock", **kwargs)
        assert r.status_code == 200, r.text
        assert r.cookies.get("taos_session")
        bare_console.cookies.clear()
        # swipe NOT chosen: the header gets it past M5 and no further.
        _force_method(app, "owner", "password")
        r = await bare_console.post("/auth/swipe-unlock", **kwargs)
        assert r.status_code == 403
        assert r.json()["error"] == "swipe unlock is not available"
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_the_header_does_not_open_it_from_the_lan(self, app):
        _force_method(app, "owner", "swipe")
        async with _client(app, "192.168.1.10") as lan_with_header:
            r = await lan_with_header.post("/auth/swipe-unlock", json={})
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_the_header_does_not_excuse_a_cross_origin_page(self, app, console):
        _force_method(app, "owner", "swipe")
        r = await console.post("/auth/swipe-unlock", json={},
                               headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403
        assert "taos_session" not in r.cookies

    def test_the_page_sends_the_header_on_swipe_unlock(self):
        import re

        from tinyagentos.routes import auth as auth_routes

        js = open(auth_routes.__file__, encoding="utf-8").read()
        calls = re.findall(r'fetch\("/auth/swipe-unlock",\s*\{(.*?)\}\)', js, flags=re.S)
        assert len(calls) == 1, calls
        assert 'method: "POST"' in calls[0]
        assert '"X-taOS-Console": "1"' in calls[0]


# --------------------------------------------------------------------------- #
#  PUT /api/settings/lock -- re-auth, scope and validation                     #
# --------------------------------------------------------------------------- #

class TestPutLockRefuses:

    @pytest.mark.asyncio
    async def test_signed_out_is_refused(self, console):
        r = await _put(console, unlock_method="swipe", current_password=PASSWORD)
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_password_is_403(self, app, console):
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="swipe")
        assert r.status_code == 403
        assert _mgr(app).unlock_method("owner") == "password"

    @pytest.mark.asyncio
    async def test_wrong_password_is_403(self, app, console):
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="swipe", current_password="nope nope nope")
        assert r.status_code == 403
        assert _mgr(app).unlock_method("owner") == "password"

    @pytest.mark.asyncio
    async def test_another_users_password_does_not_count(self, app, console):
        bob_pw = _add_second_user(app)
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="password", current_password=bob_pw)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_no_csrf_header_is_refused(self, app, console):
        _signed_in(console, app, "owner")
        r = await console.put("/api/settings/lock",
                              json={"unlock_method": "swipe", "current_password": PASSWORD})
        assert r.status_code == 403
        assert _mgr(app).unlock_method("owner") == "password"

    @pytest.mark.asyncio
    async def test_a_session_changes_only_its_own_user(self, app, console):
        """Bob's session, Bob's password, a username in the body: owner untouched."""
        bob_pw = _add_second_user(app)
        _mgr(app).set_pin("owner", "4913")
        _signed_in(console, app, "bob")
        r = await _put(console, unlock_method="password", current_password=bob_pw,
                       username="owner")
        assert r.status_code == 200
        assert _mgr(app).find_user("owner").get("unlock_method") is None
        assert _mgr(app).unlock_method("owner") == "pin"
        assert _mgr(app).find_user("bob")["unlock_method"] == "password"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["face", "", None, 3, "SWIPE", ["swipe"]])
    async def test_unknown_method_is_400(self, app, console, method):
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method=method, current_password=PASSWORD)
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_pattern_is_400_until_implemented(self, app, console):
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="pattern", current_password=PASSWORD)
        assert r.status_code == 400
        assert _mgr(app).find_user("owner").get("unlock_method") is None

    @pytest.mark.asyncio
    async def test_non_object_body_is_400(self, app, console):
        _signed_in(console, app, "owner")
        r = await console.put("/api/settings/lock", content=b"[1]",
                              headers={"X-CSRF-Token": CSRF, "Content-Type": "application/json"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_pin_without_a_pin_is_409(self, app, console):
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="pin", current_password=PASSWORD)
        assert r.status_code == 409
        assert r.json()["needs_pin"] is True

    @pytest.mark.asyncio
    async def test_swipe_on_a_multi_user_install_is_409(self, app, console):
        _add_second_user(app)
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="swipe", current_password=PASSWORD)
        assert r.status_code == 409
        assert _mgr(app).find_user("owner").get("unlock_method") is None

    @pytest.mark.asyncio
    async def test_wrong_passwords_are_throttled(self, app, console):
        _signed_in(console, app, "owner")
        for _ in range(5):
            await _put(console, unlock_method="swipe", current_password="wrong wrong")
        r = await _put(console, unlock_method="swipe", current_password=PASSWORD)
        assert r.status_code == 429
        assert _mgr(app).unlock_method("owner") == "password"


# --------------------------------------------------------------------------- #
#  pin-login honours the chosen method                                         #
# --------------------------------------------------------------------------- #

class TestPinLoginFollowsTheMethod:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["password", "swipe"])
    async def test_a_correct_pin_stops_working_when_the_method_is_not_pin(
        self, app, console, method,
    ):
        """"Disable my pin entry": the PIN still exists, but must not open."""
        _mgr(app).set_pin("owner", "4913")
        _force_method(app, "owner", method)
        r = await console.post("/auth/pin-login", json={"pin": "4913"})
        assert r.status_code == 404
        assert "taos_session" not in r.cookies


# --------------------------------------------------------------------------- #
#  The lock screen page                                                        #
# --------------------------------------------------------------------------- #

class TestLoginPageRendersTheMethod:

    @pytest.mark.asyncio
    async def test_swipe_user_with_no_pin_gets_the_lock_screen(self, app, console):
        _force_method(app, "owner", "swipe")
        assert not _mgr(app).has_pin()
        page = (await console.get("/auth/login")).text
        assert 'class="lockscreen"' in page
        assert 'data-unlock="swipe"' in page
        assert 'id="pin-panel"' not in page
        assert "Swipe up to open" in page

    @pytest.mark.asyncio
    async def test_password_user_gets_the_lock_screen_without_a_keypad(self, app, console):
        page = (await console.get("/auth/login")).text
        assert 'data-unlock="password"' in page
        assert 'id="pin-panel"' not in page
        assert 'id="pw-panel"' in page

    @pytest.mark.asyncio
    async def test_pin_user_keeps_the_keypad(self, app, console):
        _mgr(app).set_pin("owner", "4913")
        page = (await console.get("/auth/login")).text
        assert 'data-unlock="pin"' in page
        assert 'id="pin-panel"' in page

    @pytest.mark.asyncio
    async def test_the_lan_never_sees_the_lock_screen(self, app, lan):
        _force_method(app, "owner", "swipe")
        page = (await lan.get("/auth/login")).text
        assert 'class="lockscreen"' not in page
        assert "data-unlock" not in page

    @pytest.mark.asyncio
    async def test_multi_user_console_gets_the_card(self, app, console):
        _force_method(app, "owner", "swipe")
        _add_second_user(app)
        page = (await console.get("/auth/login")).text
        assert 'class="lockscreen"' not in page

    @pytest.mark.asyncio
    async def test_status_advertises_the_keypad_only_for_pin(self, app, console):
        _mgr(app).set_pin("owner", "4913")
        assert (await console.get("/auth/status")).json()["pin_available"] is True
        _force_method(app, "owner", "password")
        assert (await console.get("/auth/status")).json()["pin_available"] is False


# --------------------------------------------------------------------------- #
#  Happy paths                                                                 #
# --------------------------------------------------------------------------- #

class TestHappyPaths:

    @pytest.mark.asyncio
    async def test_owner_chooses_swipe_then_swipe_opens_the_desktop(self, app, console):
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="swipe", current_password=PASSWORD)
        assert r.status_code == 200, r.text
        assert r.json()["unlock_method"] == "swipe"

        console.cookies.clear()
        r = await console.post("/auth/swipe-unlock")
        assert r.status_code == 200
        token = r.cookies.get("taos_session")
        assert token
        set_cookie = r.headers["set-cookie"].lower()
        assert "httponly" in set_cookie and "samesite=strict" in set_cookie
        assert "max-age=" in set_cookie  # long-lived, like pin-login

        # The desktop accepts it ...
        r = await console.get("/api/settings/lock")
        assert r.status_code == 200
        assert r.json()["unlock_method"] == "swipe"
        # ... but only from the same User-Agent (#3120 binding).
        r = await console.get("/api/settings/lock", headers={"User-Agent": "curl/8"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_switching_back_to_pin_requires_the_pin_again(self, app, console):
        _mgr(app).set_pin("owner", "4913")
        _force_method(app, "owner", "swipe")
        _signed_in(console, app, "owner")
        r = await _put(console, unlock_method="pin", current_password=PASSWORD)
        assert r.status_code == 200
        console.cookies.clear()

        assert (await console.post("/auth/swipe-unlock")).status_code == 403
        assert (await console.post("/auth/pin-login", json={"pin": "0000"})).status_code == 401
        r = await console.post("/auth/pin-login", json={"pin": "4913"})
        assert r.status_code == 200
        assert r.cookies.get("taos_session")

    @pytest.mark.asyncio
    async def test_get_reports_the_defaults_for_an_existing_user(self, app, console):
        _signed_in(console, app, "owner")
        body = (await console.get("/api/settings/lock")).json()
        assert body["unlock_method"] == "password"
        assert body["has_pin"] is False
        assert body["swipe_available"] is True
        _mgr(app).set_pin("owner", "4913")
        body = (await console.get("/api/settings/lock")).json()
        assert body["unlock_method"] == "pin"
        assert body["has_pin"] is True


class TestEffectiveMethod:

    def test_defaults(self):
        assert effective_unlock_method({"pin_hash": "x"}) == "pin"
        assert effective_unlock_method({}) == "password"
        assert effective_unlock_method(None) == "password"

    def test_pin_without_a_pin_degrades_to_password(self):
        assert effective_unlock_method({"unlock_method": "pin"}) == "password"

    @pytest.mark.parametrize("junk", ["pattern", "SWIPE", " swipe", 1, None, ["swipe"]])
    def test_junk_never_becomes_swipe(self, junk):
        assert effective_unlock_method({"unlock_method": junk}) == "password"
        assert effective_unlock_method({"unlock_method": junk, "pin_hash": "x"}) == "pin"

    def test_setter_refuses_unimplemented_and_pinless_pin(self, tmp_path):
        mgr = AuthManager(tmp_path)
        mgr.setup_user("owner", "", "", PASSWORD)
        with pytest.raises(ValueError):
            mgr.set_unlock_method("owner", "pattern")
        with pytest.raises(ValueError):
            mgr.set_unlock_method("owner", "pin")
        mgr.set_unlock_method("owner", "swipe")
        assert AuthManager(tmp_path).unlock_method("owner") == "swipe"
