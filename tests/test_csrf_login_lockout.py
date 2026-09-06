"""A stale session cookie must not lock a user out of signing in (#2081).

`verify_csrf` is attached ROUTER-WIDE in `tinyagentos/routes/__init__.py`
(`app.include_router(auth_router, dependencies=_csrf)`), so it guards
`POST /auth/login`, `/auth/setup` and `/auth/pin-login` even though none of
those routes carries a visible decorator. Reading `routes/auth.py` alone tells
you the opposite; route introspection on the BUILT app is what shows it.

Its exemption was `if not conn.cookies.get("taos_session"): return` — a proxy
for "not signed in" that inverts exactly when a browser still holds an EXPIRED
cookie. That is the one moment you need to sign in again, and it answered 403
"CSRF token missing". On a keyboard-less kiosk that is unrecoverable by
retrying.

WHY THIS FILE IS NAMED `test_csrf_*`
------------------------------------
`tests/conftest.py` installs an autouse fixture that replaces `verify_csrf`
with a no-op for EVERY test file whose path does not contain "test_csrf". A
test of this defect written as a normal test file measures the FIXTURE, not the
system, and passes green against the broken code. `test_bypass_fixture_is_not_active`
below fails loudly if a rename ever puts this file back under the bypass.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager

# A cookie the server will not recognise: the shape a browser presents after
# the session behind it has expired or been purged server-side.
STALE_SESSION = "stale-session-token-that-no-longer-resolves"

PASSWORD = "correct horse battery staple"
PIN = "4913"


@pytest.fixture(autouse=True)
def _clean_pin_throttle():
    """The route module's PIN limiter is a process-wide singleton. Reset it
    around every test so one test's deliberate failures cannot throttle the
    next one."""
    from tinyagentos.routes import auth as auth_routes

    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    yield
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()


@pytest.fixture()
def configured_app(tmp_path, monkeypatch):
    """An app with one existing account that has both a password and a PIN."""
    from tinyagentos.app import create_app

    monkeypatch.setenv("TINYAGENTOS_DATA_DIR", str(tmp_path))
    app = create_app()
    mgr = AuthManager(tmp_path)
    mgr.setup_user("tester", "Bring-up Test", "", PASSWORD)
    mgr.set_pin("tester", PIN)
    app.state.auth = mgr
    return app


@pytest.fixture()
def unconfigured_app(tmp_path, monkeypatch):
    """An app with NO account yet — the first-run setup surface."""
    from tinyagentos.app import create_app

    monkeypatch.setenv("TINYAGENTOS_DATA_DIR", str(tmp_path))
    app = create_app()
    app.state.auth = AuthManager(tmp_path)
    return app


def _console_client(app, cookies):
    """A client that looks like the device's own screen, carrying `cookies`."""
    transport = ASGITransport(app=app, client=("127.0.0.1", 51234))
    return AsyncClient(
        transport=transport,
        base_url="http://localhost:6969",
        cookies=cookies,
    )


@pytest_asyncio.fixture()
async def stale_console(configured_app):
    async with _console_client(
        configured_app, {"taos_session": STALE_SESSION}
    ) as c:
        yield c


def test_bypass_fixture_is_not_active():
    """This module must run against the REAL verify_csrf.

    conftest's autouse `_bypass_csrf_in_tests` no-ops verify_csrf for every file
    whose path lacks "test_csrf". If this file is ever renamed out of that
    carve-out, every assertion below would pass vacuously while measuring
    nothing. Assert the real function is installed rather than trusting the
    filename.
    """
    from tinyagentos.middleware import csrf

    assert csrf.verify_csrf.__name__ == "verify_csrf"
    assert csrf.verify_csrf.__module__ == "tinyagentos.middleware.csrf"


class TestStaleCookieDoesNotBlockSignIn:
    """Both sign-in surfaces, because fixing only one leaves a locked kiosk.

    The password form and PIN sign-in are separate routes reached by separate
    clients. An assertion on the password form alone would pass while PIN
    sign-in — the ONLY surface a keyboard-less kiosk can use — stayed broken.
    """

    @pytest.mark.asyncio
    async def test_pin_login_succeeds_with_a_stale_session_cookie(
        self, stale_console
    ):
        resp = await stale_console.post(
            "/auth/pin-login", json={"username": "tester", "pin": PIN}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True

    @pytest.mark.asyncio
    async def test_password_login_succeeds_with_a_stale_session_cookie(
        self, stale_console
    ):
        resp = await stale_console.post(
            "/auth/login",
            data={"username": "tester", "password": PASSWORD},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text
        assert resp.headers["location"] == "/desktop"

    @pytest.mark.asyncio
    async def test_first_run_setup_is_not_blocked_by_a_stale_session_cookie(
        self, unconfigured_app
    ):
        """A wiped data dir plus a browser that still holds the old cookie is a
        real bring-up state, and it must not answer 403."""
        async with _console_client(
            unconfigured_app, {"taos_session": STALE_SESSION}
        ) as c:
            resp = await c.post(
                "/auth/setup",
                json={
                    "username": "tester",
                    "display_name": "Bring-up Test",
                    "email": "",
                    "password": PASSWORD,
                },
                follow_redirects=False,
            )
        assert resp.status_code != 403, resp.text

    @pytest.mark.asyncio
    async def test_first_boot_wizard_is_not_blocked_by_a_stale_session_cookie(
        self, unconfigured_app
    ):
        """`POST /setup/complete` lives on the dashboard router, not the auth
        one, but carries the same blanket dependency and the same defect: it is
        a form POST that sets the password and mints a session, so it can never
        attach an X-CSRF-Token header."""
        async with _console_client(
            unconfigured_app, {"taos_session": STALE_SESSION}
        ) as c:
            resp = await c.post(
                "/setup/complete",
                data={"password": PASSWORD},
                follow_redirects=False,
            )
        assert resp.status_code != 403, resp.text


class TestExemptionsStayWithinTheUnauthenticatedSurface:
    """`_CREDENTIAL_PATHS` must be a SUBSET of the session gate's exempt list.

    A path can only need a CSRF exemption if it is reachable with no
    credential at all. Anything outside `EXEMPT_PATHS` sits behind the session
    gate, so a stale cookie never reaches it and exempting it would be pure
    loss. Asserting containment stops the two lists drifting apart, and stops
    the exemption list quietly growing into a hole.
    """

    def test_every_exempt_path_is_reachable_without_a_session(self):
        from tinyagentos.auth_middleware import EXEMPT_PATHS
        from tinyagentos.middleware.csrf import _CREDENTIAL_PATHS

        assert _CREDENTIAL_PATHS <= set(EXEMPT_PATHS), (
            "CSRF-exempt paths not on the session gate's exempt list: "
            f"{sorted(_CREDENTIAL_PATHS - set(EXEMPT_PATHS))}"
        )


class TestPinPanelCanAlwaysRenderTheReason:
    """Every failure `/auth/pin-login` can return must carry an `error` key.

    `_PIN_PANEL_SCRIPT` used to render
    `(res.body && res.body.error) || "Incorrect PIN."`, so a body without an
    `error` key surfaced as "Incorrect PIN." — a CORRECT PIN refused for an
    unrelated reason accused the user of typing it wrong, on a device with no
    other way in. That expression now also reads `detail`, with a fallback that
    does not blame the PIN:

        var reason = res.body && (res.body.error || res.body.detail);
        fail(reason || "Sign-in failed. Try again, or use your password.");

    The client change is the backstop, not the contract. These tests assert the
    contract at the SOURCE of the body — every failure the route can return
    names itself in `error` — because a panel that can only ever say "something
    went wrong" is still a dead end on a kiosk. Asserting the string the panel
    prints would be one level too coarse: it passes as long as *some* text
    appears, including text that identifies nothing.
    """

    @pytest.mark.asyncio
    async def test_wrong_pin_carries_error(self, configured_app):
        async with _console_client(configured_app, {}) as c:
            resp = await c.post(
                "/auth/pin-login", json={"username": "tester", "pin": "0000"}
            )
        assert resp.status_code == 401
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_non_object_body_carries_error(self, configured_app):
        async with _console_client(configured_app, {}) as c:
            resp = await c.post(
                "/auth/pin-login",
                content=b"null",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_off_console_refusal_carries_error(self, configured_app):
        transport = ASGITransport(app=configured_app, client=("192.168.1.10", 51234))
        async with AsyncClient(
            transport=transport, base_url="http://taos.local:6969"
        ) as c:
            resp = await c.post(
                "/auth/pin-login", json={"username": "tester", "pin": PIN}
            )
        assert resp.status_code == 404
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_stale_cookie_never_answers_a_bare_detail_body(
        self, stale_console
    ):
        """The regression itself, stated as the panel sees it.

        Before the fix this was 403 `{"detail": "CSRF token missing"}` — no
        `error` key — which the panel rendered as "Incorrect PIN." for a PIN
        that was correct.
        """
        resp = await stale_console.post(
            "/auth/pin-login", json={"username": "tester", "pin": PIN}
        )
        body = resp.json()
        if resp.status_code >= 400:
            assert "error" in body, (
                f"status {resp.status_code} body {body!r} has no 'error' key, so "
                "pin-panel.js renders it as the literal 'Incorrect PIN.'"
            )


class TestCsrfStillGuardsSessionAuthenticatedRoutes:
    """The exemption must not become a hole.

    Only the routes that ESTABLISH a credential are exempt. Anything that acts
    on an already-valid session still requires the double-submit token.
    """

    @pytest.mark.asyncio
    async def test_logout_still_requires_the_token(self, configured_app):
        record = configured_app.state.auth.find_user("tester")
        token = configured_app.state.auth.create_session(
            user_id=record["id"], long_lived=False
        )
        async with _console_client(
            configured_app, {"taos_session": token, "csrf_token": "abc123"}
        ) as c:
            resp = await c.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_setting_a_pin_still_requires_the_token(self, configured_app):
        record = configured_app.state.auth.find_user("tester")
        token = configured_app.state.auth.create_session(
            user_id=record["id"], long_lived=False
        )
        async with _console_client(
            configured_app, {"taos_session": token, "csrf_token": "abc123"}
        ) as c:
            resp = await c.post("/auth/pin", json={"pin": "1234", "password": PASSWORD})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_password_change_still_requires_the_token(self, configured_app):
        """A user-admin mutation with no visible decorator — it is protected
        only by the router-wide dependency, so the exemption list is the only
        thing standing between it and an open door."""
        record = configured_app.state.auth.find_user("tester")
        token = configured_app.state.auth.create_session(
            user_id=record["id"], long_lived=False
        )
        async with _console_client(
            configured_app, {"taos_session": token, "csrf_token": "abc123"}
        ) as c:
            resp = await c.post(
                "/auth/users/tester/password", json={"password": "another pass 123"}
            )
        assert resp.status_code == 403
