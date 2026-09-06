"""PIN sign-in at the HTTP layer.

`test_auth_pin.py` proves the origin RULE in isolation. These tests prove the
routes actually APPLY it — a correct rule that no endpoint consults would pass
every unit test in the other file while leaving the door open.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager


@pytest.fixture(autouse=True)
def _clean_pin_throttle():
    """The route module's PIN limiter is a process-wide singleton (correct for a
    real deployment, which is one process). Reset it around every test so one
    test's deliberate failures cannot throttle the next one."""
    from tinyagentos.routes import auth as auth_routes

    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    yield
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()


@pytest.fixture()
def pin_app(tmp_path, monkeypatch):
    from tinyagentos.app import create_app

    monkeypatch.setenv("TINYAGENTOS_DATA_DIR", str(tmp_path))
    app = create_app()
    mgr = AuthManager(tmp_path)
    mgr.setup_user("tester", "Bring-up Test", "", "correct horse battery staple")
    mgr.set_pin("tester", "4913")
    app.state.auth = mgr
    return app


@pytest_asyncio.fixture()
async def console(pin_app):
    """A client that looks like the device's own screen."""
    transport = ASGITransport(app=pin_app, client=("127.0.0.1", 51234))
    async with AsyncClient(transport=transport, base_url="http://localhost:6969") as c:
        yield c


@pytest_asyncio.fixture()
async def lan(pin_app):
    """A client on the home network, i.e. NOT the console."""
    transport = ASGITransport(app=pin_app, client=("192.168.1.10", 51234))
    async with AsyncClient(transport=transport, base_url="http://taos.local:6969") as c:
        yield c


class TestPinLoginIsConsoleOnly:
    """The refusing direction, measured over HTTP."""

    @pytest.mark.asyncio
    async def test_correct_pin_from_lan_is_refused(self, lan):
        r = await lan.post("/auth/pin-login", json={"username": "tester", "pin": "4913"})
        assert r.status_code == 404
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_correct_pin_from_console_signs_in(self, console):
        r = await console.post("/auth/pin-login", json={"username": "tester", "pin": "4913"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.cookies.get("taos_session")

    @pytest.mark.asyncio
    async def test_forwarded_console_request_is_refused(self, console):
        """A proxied request reaches the app from loopback; it is not the console."""
        r = await console.post(
            "/auth/pin-login",
            json={"username": "tester", "pin": "4913"},
            headers={"X-Forwarded-For": "192.168.1.10"},
        )
        assert r.status_code == 404
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_lan_cannot_spoof_its_way_to_console(self, lan):
        r = await lan.post(
            "/auth/pin-login",
            json={"username": "tester", "pin": "4913"},
            headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
        )
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_offconsole_refusal_does_not_reveal_that_a_pin_exists(self, lan):
        """404 for 'no PIN here' and 404 for 'not allowed' must be the same answer."""
        r = await lan.post("/auth/pin-login", json={"username": "tester", "pin": "4913"})
        body = r.text.lower()
        assert "incorrect" not in body
        assert "too many" not in body


class TestPinLoginCredentialChecks:
    @pytest.mark.asyncio
    async def test_wrong_pin_from_console_is_401(self, console):
        r = await console.post("/auth/pin-login", json={"username": "tester", "pin": "0000"})
        assert r.status_code == 401
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_password_is_not_accepted_as_a_pin(self, console):
        r = await console.post(
            "/auth/pin-login",
            json={"username": "tester", "pin": "correct horse battery staple"},
        )
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_throttle_engages_and_reports_retry_after(self, console):
        for _ in range(5):
            await console.post("/auth/pin-login", json={"username": "tester", "pin": "0000"})
        r = await console.post("/auth/pin-login", json={"username": "tester", "pin": "0000"})
        assert r.status_code == 429
        assert int(r.headers["Retry-After"]) > 0

    @pytest.mark.asyncio
    async def test_throttle_blocks_even_the_correct_pin(self, console):
        """Otherwise the delay is trivially bypassed by guessing correctly."""
        for _ in range(5):
            await console.post("/auth/pin-login", json={"username": "tester", "pin": "0000"})
        r = await console.post("/auth/pin-login", json={"username": "tester", "pin": "4913"})
        assert r.status_code == 429

    @pytest.mark.asyncio
    async def test_pin_failures_do_not_lock_out_the_password(self, console):
        """R4 over HTTP: the two factors must not share a lockout budget."""
        for _ in range(12):
            await console.post("/auth/pin-login", json={"username": "tester", "pin": "0000"})
        r = await console.post(
            "/auth/login",
            json={"username": "tester", "password": "correct horse battery staple"},
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True


class TestStatusAdvertisesPin:
    @pytest.mark.asyncio
    async def test_console_is_offered_the_keypad(self, console):
        r = await console.get("/auth/status")
        assert r.json()["pin_available"] is True

    @pytest.mark.asyncio
    async def test_lan_is_not_told_a_pin_exists(self, lan):
        r = await lan.get("/auth/status")
        assert r.json()["pin_available"] is False

    @pytest.mark.asyncio
    async def test_forwarded_request_is_not_offered_the_keypad(self, console):
        r = await console.get("/auth/status", headers={"X-Forwarded-For": "192.168.1.10"})
        assert r.json()["pin_available"] is False

    @pytest.mark.asyncio
    async def test_no_pin_configured_is_not_advertised(self, pin_app, console):
        pin_app.state.auth.clear_pin("tester")
        r = await console.get("/auth/status")
        assert r.json()["pin_available"] is False


@pytest.fixture()
def fresh_app(tmp_path, monkeypatch):
    """An install with NO user yet, so /auth/setup is live."""
    from tinyagentos.app import create_app

    monkeypatch.setenv("TINYAGENTOS_DATA_DIR", str(tmp_path))
    app = create_app()
    app.state.auth = AuthManager(tmp_path)
    return app


@pytest_asyncio.fixture()
async def fresh_console(fresh_app):
    transport = ASGITransport(app=fresh_app, client=("127.0.0.1", 51234))
    async with AsyncClient(transport=transport, base_url="http://localhost:6969") as c:
        yield c


@pytest_asyncio.fixture()
async def fresh_lan(fresh_app):
    transport = ASGITransport(app=fresh_app, client=("192.168.1.10", 51234))
    async with AsyncClient(transport=transport, base_url="http://taos.local:6969") as c:
        yield c


class TestSetupOffersAPinAtInstall:
    """Jay's ask is a choice of sign-in method AT INSTALL as well as in
    Settings. A touchscreen with no keyboard has to be able to leave the
    first-run wizard with a PIN already usable."""

    @pytest.mark.asyncio
    async def test_console_setup_offers_a_pin(self, fresh_console):
        r = await fresh_console.get("/auth/setup")
        assert r.status_code == 200
        assert 'id="setup-pin"' in r.text

    @pytest.mark.asyncio
    async def test_lan_setup_does_not_offer_a_pin(self, fresh_lan):
        """Off-console a PIN is refused, so offering the field would hand the
        user a method that cannot work."""
        r = await fresh_lan.get("/auth/setup")
        assert r.status_code == 200
        assert 'id="setup-pin"' not in r.text
        assert "name=\"pin\"" not in r.text

    @pytest.mark.asyncio
    async def test_pin_set_at_install_signs_in(self, fresh_app, fresh_console):
        r = await fresh_console.post("/auth/setup", json={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
            "pin": "4913",
        })
        assert r.status_code == 200, r.text
        assert fresh_app.state.auth.has_pin("tester") is True
        r2 = await fresh_console.post("/auth/pin-login", json={"pin": "4913"})
        assert r2.status_code == 200
        assert r2.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_setup_without_a_pin_still_works(self, fresh_app, fresh_console):
        """The PIN is optional. Requiring it would be a new way to be locked
        out, which is the opposite of the point."""
        r = await fresh_console.post("/auth/setup", json={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
        })
        assert r.status_code == 200
        assert fresh_app.state.auth.has_pin("tester") is False

    @pytest.mark.asyncio
    async def test_a_bad_pin_creates_no_account(self, fresh_app, fresh_console):
        """/auth/setup only works while zero users exist, so an account created
        alongside a rejected PIN could never be retried — it would answer 409."""
        r = await fresh_console.post("/auth/setup", json={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
            "pin": "12",
        })
        assert r.status_code == 400
        assert fresh_app.state.auth.is_configured() is False

    @pytest.mark.asyncio
    async def test_lan_cannot_set_a_pin_at_install(self, fresh_app, fresh_lan):
        r = await fresh_lan.post("/auth/setup", json={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
            "pin": "4913",
        })
        assert r.status_code == 400
        assert fresh_app.state.auth.is_configured() is False

    @pytest.mark.asyncio
    async def test_form_setup_sets_the_pin(self, fresh_app, fresh_console):
        r = await fresh_console.post("/auth/setup", data={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
            "pin": "4913", "auto_login": "1",
        })
        assert r.status_code == 303
        assert fresh_app.state.auth.has_pin("tester") is True

    @pytest.mark.asyncio
    async def test_form_setup_rejects_a_bad_pin_before_creating_the_user(
        self, fresh_app, fresh_console
    ):
        r = await fresh_console.post("/auth/setup", data={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
            "pin": "abcd",
        })
        assert r.status_code == 303
        assert "error=pin" in r.headers["location"]
        assert fresh_app.state.auth.is_configured() is False

    @pytest.mark.asyncio
    async def test_form_setup_off_console_drops_the_pin_but_still_onboards(
        self, fresh_app, fresh_lan
    ):
        """The field is not rendered off-console, so anything arriving in it was
        not typed by this user — drop it rather than fail their onboarding."""
        r = await fresh_lan.post("/auth/setup", data={
            "username": "tester", "full_name": "T", "password": "correct horse battery",
            "pin": "4913",
        })
        assert r.status_code == 303
        assert fresh_app.state.auth.is_configured() is True
        assert fresh_app.state.auth.has_pin("tester") is False


class TestConsoleIsNeverBrickedByAMissingScript:
    """The login page must be usable with NO JavaScript at all.

    CodeRabbit raised this on #2540 and it is the whole point of the card: the
    page used to render the password form with a static `hidden` attribute
    whenever a PIN existed, and ONLY /auth/pin-panel.js could reveal it. A CSP
    refusal, a cache miss or JS-off therefore left a keypad that cannot submit
    and no password form -- the exact hard lockout PIN sign-in was built to
    remove. The server now renders the password form VISIBLE and the PIN panel
    HIDDEN; the script swaps them once it is wired.
    """

    @pytest.mark.asyncio
    async def test_password_form_is_visible_without_scripts(self, console):
        r = await console.get("/auth/login")
        assert r.status_code == 200
        body = r.text
        # The pw-panel form tag must carry no `hidden` attribute.
        start = body.index('<form class="pw-panel"')
        tag = body[start : body.index(">", start)]
        assert "hidden" not in tag, f"password form is hidden without JS: {tag}"

    @pytest.mark.asyncio
    async def test_pin_panel_starts_hidden(self, console):
        r = await console.get("/auth/login")
        start = r.text.index('<div class="pin-panel"')
        tag = r.text[start : r.text.index(">", start)]
        assert "hidden" in tag, f"pin panel must start hidden: {tag}"

    @pytest.mark.asyncio
    async def test_script_reveals_the_pin_panel_at_init(self, console):
        """The reveal must happen at INIT, not only on a click.

        Asserting merely that "swap(true)" appears somewhere is one level
        coarser than the defect and passes on the broken code, because the
        `use-pin` click handler has always contained that call. What matters
        is that init() ENDS by swapping, so the panel appears with no user
        gesture. Anchor on init's closing statement.
        """
        r = await console.get("/auth/pin-panel.js")
        assert r.status_code == 200
        assert "swap(true);\n  }" in r.text, (
            "init() does not end by revealing the PIN panel; the panel would "
            "stay hidden until the user finds the toggle"
        )


class TestNonObjectJsonBodyIsABadRequest:
    """`request.json()` accepts null/[]/1/"x" -- none of them a mapping.

    Without an explicit check the first body.get() raises AttributeError and
    the caller gets a 500 for a plainly malformed request. /auth/pin-login is
    session-exempt, so any console client can reach it with the body `null`.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", ["null", "[]", "1", '"x"'])
    async def test_pin_login_rejects_non_object_body(self, console, body):
        r = await console.post(
            "/auth/pin-login",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 400, f"{body!r} gave {r.status_code}, want 400"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", ["null", "[]", "1", '"x"'])
    async def test_set_pin_rejects_non_object_body(self, console, body):
        """Unauthenticated here, so it 401s first -- but it must never 500."""
        r = await console.post(
            "/auth/pin",
            content=body,
            headers={"content-type": "application/json"},
        )
        assert r.status_code in (400, 401), f"{body!r} gave {r.status_code}"
