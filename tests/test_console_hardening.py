"""Hardening of the console lock: sign-in gates, enforced lock, console secret,
persistent PIN throttle, and swipe-mode power actions.

Every test drives the real routes (ASGI app, middleware included).
"""
from __future__ import annotations

import json
import os
import re
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.auth import AuthManager

PASSWORD = "correct horse battery staple"
PIN = "4913"
UA = "Mozilla/5.0 (Linux; taOS kiosk) Chromium/131"
SECRET = "unit-test-console-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    from tinyagentos.routes import auth as auth_routes

    secret_file = tmp_path / "console-secret"
    secret_file.write_text(SECRET + "\n")
    os.chmod(secret_file, 0o600)
    monkeypatch.setenv("TAOS_CONSOLE_SECRET_FILE", str(secret_file))
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    auth_routes._CONSOLE_LOCKED = True
    yield
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    auth_routes._CONSOLE_LOCKED = True


@pytest.fixture()
def app(tmp_path):
    from tinyagentos.app import create_app

    application = create_app(data_dir=tmp_path)
    mgr = AuthManager(tmp_path)
    mgr.setup_user("owner", "Device Owner", "", PASSWORD)
    mgr.set_pin("owner", PIN)
    application.state.auth = mgr
    return application


def _client(app, host="127.0.0.1", header=SECRET, **extra):
    headers = {"User-Agent": UA}
    if header is not None:
        headers["X-taOS-Console"] = header
    headers.update(extra)
    transport = ASGITransport(app=app, client=(host, 51234))
    return AsyncClient(transport=transport, base_url="http://localhost:6969", headers=headers)


@pytest_asyncio.fixture()
async def console(app):
    async with _client(app) as c:
        yield c


async def _open_shell(client):
    """What the shell launcher does: mint a ticket with the secret, open the
    start URL with it, and keep the console cookie that comes back."""
    t = await client.post("/auth/console-ticket", headers={"X-taOS-Console": SECRET})
    assert t.status_code == 200, t.text
    r = await client.get("/?console_ticket=" + t.json()["ticket"], follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/", (r.status_code, r.text[:200])
    assert client.cookies.get("taos_console")


async def _screen_off(app):
    """The handset's power script: no cookie, the secret in the header."""
    async with _client(app) as script:
        r = await script.post("/auth/lock-screen-off")
        assert r.status_code == 200, r.text


async def _pin_in(client):
    r = await client.post("/auth/pin-login", json={"username": "owner", "pin": PIN})
    assert r.status_code == 200, r.text
    return r.cookies["taos_session"]


# --------------------------------------------------------------------------- #
#  G20: pin-login has the same gate as swipe-unlock                           #
# --------------------------------------------------------------------------- #

class TestPinLoginGate:
    @pytest.mark.asyncio
    async def test_foreign_origin_is_refused(self, console):
        r = await console.post(
            "/auth/pin-login", json={"username": "owner", "pin": PIN},
            headers={"Origin": "http://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert r.status_code == 403, r.text
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_text_plain_simple_request_is_refused(self, app):
        async with _client(app, header=None) as bare:
            # The shell's own cookie identifies the console; the request is
            # still a simple one (no custom header, text/plain).
            await _open_shell(bare)
            r = await bare.post(
                "/auth/pin-login",
                content=json.dumps({"username": "owner", "pin": PIN}),
                headers={"Content-Type": "text/plain;charset=UTF-8"},
            )
        assert r.status_code == 403, r.text
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_json_only_even_with_the_custom_header(self, console):
        r = await console.post(
            "/auth/pin-login",
            content=json.dumps({"username": "owner", "pin": PIN}),
            headers={"Content-Type": "text/plain"},
        )
        assert r.status_code in (400, 415), r.text
        assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_refused_requests_do_not_touch_the_throttle(self, console):
        for _ in range(12):
            await console.post(
                "/auth/pin-login", json={"username": "owner", "pin": "0000"},
                headers={"Origin": "http://evil.example"},
            )
        r = await console.post("/auth/pin-login", json={"username": "owner", "pin": PIN})
        assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
#  G17 / G18: "locked" is enforced on the server                              #
# --------------------------------------------------------------------------- #

class TestLockIsEnforced:
    @pytest.mark.asyncio
    async def test_locked_console_session_reaches_nothing(self, app, console):
        await _pin_in(console)
        assert (await console.get("/api/settings/lock")).status_code == 200
        await _screen_off(app)
        assert (await console.get("/api/settings/lock")).status_code == 401
        status = (await console.get("/auth/status")).json()
        assert status.get("authenticated") is False

    @pytest.mark.asyncio
    async def test_unlock_restores_access(self, app, console):
        await _pin_in(console)
        await _screen_off(app)
        console.cookies.clear()
        await _pin_in(console)
        assert (await console.get("/api/settings/lock")).status_code == 200

    @pytest.mark.asyncio
    async def test_sign_in_page_on_console_revokes_the_session(self, app, console):
        token = await _pin_in(console)
        await console.get("/auth/login")
        async with _client(app, host="192.168.1.10", header=None) as lan:
            r = await lan.get("/api/settings/lock", headers={"Cookie": f"taos_session={token}"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_a_console_session_is_dead_for_every_holder_after_lock(self, app, console):
        """The lock ends the session itself: replaying the cookie from a client
        that is NOT the console (so the cookie-drop above never applies) fails."""
        token = await _pin_in(console)
        await _screen_off(app)
        async with _client(app, host="192.168.1.10", header=None) as other:
            r = await other.get("/api/settings/lock", headers={"Cookie": f"taos_session={token}"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_the_shells_follow_up_lock_call_still_answers_2xx(self, app, console):
        """The hook POSTs /auth/lock after the screen-off signal already ended
        the session; it must still get a 2xx or it would retry for nothing."""
        await _pin_in(console)
        await console.get("/auth/status")
        csrf = console.cookies.get("csrf_token")
        await _screen_off(app)
        r = await console.post("/auth/lock", headers={"X-CSRF-Token": csrf or ""})
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_remote_sessions_are_not_affected_by_the_console_lock(self, app, console):
        async with _client(app, host="192.168.1.10", header=None) as lan:
            r = await lan.post("/auth/login", json={"username": "owner", "password": PASSWORD})
            assert r.status_code == 200
            await _screen_off(app)
            assert (await lan.get("/api/settings/lock")).status_code == 200


# --------------------------------------------------------------------------- #
#  G19: loopback alone is not the console                                     #
# --------------------------------------------------------------------------- #

class TestConsoleSecret:
    @pytest.mark.asyncio
    async def test_the_old_constant_header_is_not_the_console(self, app):
        async with _client(app, header="1") as old:
            assert (await old.get("/auth/lock-state")).status_code == 404
            r = await old.post("/auth/pin-login", json={"username": "owner", "pin": PIN})
            assert r.status_code == 404
            assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_no_header_at_all_is_not_the_console(self, app):
        async with _client(app, header=None) as bare:
            assert (await bare.get("/auth/lock-state")).status_code == 404

    @pytest.mark.asyncio
    async def test_the_secret_header_is_the_console(self, console):
        r = await console.get("/auth/lock-state")
        assert r.status_code == 200
        assert r.json()["locked"] is True

    @pytest.mark.asyncio
    async def test_ticket_trades_for_a_cookie_once(self, app):
        async with _client(app, header=None) as shell:
            bad = await shell.post("/auth/console-ticket", headers={"X-taOS-Console": "nope"})
            assert bad.status_code == 403
            t = await shell.post("/auth/console-ticket", headers={"X-taOS-Console": SECRET})
            ticket = t.json()["ticket"]
            assert SECRET not in ticket
            r = await shell.get("/?console_ticket=" + ticket, follow_redirects=False)
            assert r.status_code == 303
            assert "console_ticket" not in r.headers["location"]
            cookie = r.headers.get("set-cookie", "")
            assert "taos_console=" in cookie and "HttpOnly" in cookie and SECRET not in cookie
            shell.cookies.clear()
            again = await shell.get("/?console_ticket=" + ticket, follow_redirects=False)
            assert "taos_console" not in again.headers.get("set-cookie", "")
            await _open_shell(shell)
            r = await shell.get("/auth/lock-state", headers={"X-taOS-Console": "1"})
            assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_a_cookie_cannot_mint_tickets(self, app):
        async with _client(app, header=None) as shell:
            await _open_shell(shell)
            r = await shell.post("/auth/console-ticket")
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_tickets_are_loopback_only(self, app):
        async with _client(app, host="192.168.1.10", header=SECRET) as lan:
            r = await lan.post("/auth/console-ticket")
        assert r.status_code == 403
        assert "ticket" not in r.text

    def test_secret_file_is_private_and_long(self, tmp_path, monkeypatch):
        from tinyagentos import console_secret

        path = tmp_path / "fresh" / "secret"
        monkeypatch.setenv("TAOS_CONSOLE_SECRET_FILE", str(path))
        first = console_secret.get_secret()
        assert first and len(first) >= 32
        assert (path.stat().st_mode & 0o777) == 0o600
        assert console_secret.get_secret() == first

    def test_no_secret_means_not_console(self, monkeypatch, tmp_path):
        from tinyagentos import console_secret

        blocked = tmp_path / "file"
        blocked.write_text("x")
        monkeypatch.setenv("TAOS_CONSOLE_SECRET_FILE", str(blocked / "nope" / "secret"))
        assert console_secret.get_secret() is None
        assert console_secret.matches(None, {"x-taos-console": "1"}, {}) is False


# --------------------------------------------------------------------------- #
#  G16: PIN throttle survives a restart and backs off exponentially           #
# --------------------------------------------------------------------------- #

class TestPinThrottlePersists:
    @pytest.mark.asyncio
    async def test_a_restart_does_not_reset_the_failures(self, app, console):
        from tinyagentos.routes import auth as auth_routes

        for _ in range(5):
            r = await console.post("/auth/pin-login", json={"username": "owner", "pin": "0000"})
            assert r.status_code == 401
        # Controller restart: a brand-new limiter object, nothing in memory.
        auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
        r = await console.post("/auth/pin-login", json={"username": "owner", "pin": PIN})
        assert r.status_code == 429, r.text

    @pytest.mark.asyncio
    async def test_success_still_clears_it_on_disk(self, app, console):
        from tinyagentos.routes import auth as auth_routes

        for _ in range(3):
            await console.post("/auth/pin-login", json={"username": "owner", "pin": "0000"})
        await _pin_in(console)
        auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
        for _ in range(4):
            r = await console.post("/auth/pin-login", json={"username": "owner", "pin": "0000"})
            assert r.status_code == 401

    def test_delay_doubles_per_failure_past_the_threshold(self):
        from tinyagentos.auth import _PinAttemptLimiter

        got = []
        for n in (5, 6, 7, 8):
            lim = _PinAttemptLimiter()
            for _ in range(n):
                lim.record_failure("u", now=1000.0)
            got.append(lim.retry_after("u", now=1000.0))
        assert [round(x, -1) for x in got] == [30, 60, 120, 240]


# --------------------------------------------------------------------------- #
#  G8 / G7 / G13 / G14: swipe-mode actions and the lock-screen notes          #
# --------------------------------------------------------------------------- #

class TestSwipeModeActions:
    @pytest.mark.asyncio
    async def test_stop_agents_with_a_swipe_session_needs_the_password(self, app, console):
        mgr = app.state.auth
        data = mgr._read_users()
        for u in data["users"]:
            u["unlock_method"] = "swipe"
        mgr._write_users(data)
        r = await console.post("/auth/swipe-unlock", json={})
        assert r.status_code == 200, r.text
        class _Orch:
            called = False
            async def prepare(self, *a, **k):
                _Orch.called = True
                return {}
        app.state.orchestrator = _Orch()
        csrf = console.cookies.get("csrf_token")
        hdr = {"X-CSRF-Token": csrf} if csrf else {}
        r = await console.post("/auth/lock-power-action", json={"action": "stop-agents"}, headers=hdr)
        assert r.status_code in (401, 403), r.text
        assert _Orch.called is False
        r = await console.post(
            "/auth/lock-power-action",
            json={"action": "stop-agents", "password": PASSWORD}, headers=hdr,
        )
        assert r.status_code == 200, r.text
        assert _Orch.called is True


class TestLockPageIsHonest:
    def _src(self):
        from tinyagentos.routes import auth as auth_routes

        return open(auth_routes.__file__, encoding="utf-8").read()

    def test_stop_all_agents_in_swipe_mode_uses_the_password_sheet(self):
        body = re.search(r"function requirePasscodeForPower\(item\) \{(.*?)\n    \}\n", self._src(), re.S).group(1)
        assert 'UNLOCK === "swipe"' in body and "openPasswordSheet" in body

    def test_pending_intents_are_not_promised(self):
        src = self._src()
        assert 'note.textContent = "Unlock to stop all agents"' not in src
        assert 'decDone.textContent = "Unlock to " + (approved' not in src
        assert 'note.textContent = "Unlock to " + label' not in src


# --------------------------------------------------------------------------- #
#  Review round: restart, WebSocket, screenshot, ticket uid, boot id,          #
#  clock, idle expiry, kiosk without a ticket                                  #
# --------------------------------------------------------------------------- #

class TestRestartAndSockets:
    @pytest.mark.asyncio
    async def test_a_restart_ends_console_sessions(self, app, console, tmp_path):
        token = await _pin_in(console)
        # A controller restart: the flag is back to "locked" and the app starts.
        async with app.router.lifespan_context(app):
            pass
        assert app.state.auth.validate_session(token, user_agent=UA) is None
        async with _client(app, host="192.168.1.10", header=None) as other:
            r = await other.get("/api/settings/lock", headers={"Cookie": f"taos_session={token}"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_a_websocket_refuses_a_locked_console_session(self, app, console):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        token = await _pin_in(console)
        await _screen_off(app)
        with TestClient(app) as tc:
            with pytest.raises(WebSocketDisconnect):
                with tc.websocket_connect(
                    "/ws/terminal", headers={"cookie": f"taos_session={token}", "user-agent": UA}
                ) as ws:
                    ws.receive_text()


class TestScreenshotIsGated:
    @pytest.mark.asyncio
    async def test_no_session_no_screenshot(self, app, console, monkeypatch):
        from tinyagentos.routes import auth as auth_routes

        calls = []
        monkeypatch.setattr(auth_routes, "_take_screenshot", lambda: calls.append(1) or {"ok": True})
        r = await console.post("/auth/lock-power-action", json={"action": "screenshot"})
        assert r.status_code == 401, r.text
        assert calls == []

    @pytest.mark.asyncio
    async def test_swipe_session_alone_cannot_screenshot(self, app, console, monkeypatch):
        from tinyagentos.routes import auth as auth_routes

        calls = []
        monkeypatch.setattr(auth_routes, "_take_screenshot", lambda: calls.append(1) or {"ok": True})
        mgr = app.state.auth
        data = mgr._read_users()
        for u in data["users"]:
            u["unlock_method"] = "swipe"
        mgr._write_users(data)
        assert (await console.post("/auth/swipe-unlock", json={})).status_code == 200
        csrf = console.cookies.get("csrf_token")
        hdr = {"X-CSRF-Token": csrf} if csrf else {}
        r = await console.post("/auth/lock-power-action", json={"action": "screenshot"}, headers=hdr)
        assert r.status_code in (401, 403), r.text
        assert calls == []


class TestTicketIsBoundToItsUid:
    @pytest.mark.asyncio
    async def test_another_uid_cannot_redeem(self, app, monkeypatch):
        from tinyagentos import console_secret

        uids = iter([1000, 1001, 1000])
        monkeypatch.setattr(console_secret, "peer_uid", lambda host, port: next(uids), raising=False)
        async with _client(app, header=None) as shell:
            t = await shell.post("/auth/console-ticket", headers={"X-taOS-Console": SECRET})
            ticket = t.json()["ticket"]
            bad = await shell.get("/?console_ticket=" + ticket, follow_redirects=False)
            assert "taos_console" not in bad.headers.get("set-cookie", "")
            good = await shell.get("/?console_ticket=" + ticket, follow_redirects=False)
            assert "taos_console=" in good.headers.get("set-cookie", "")


class TestSecretFreshness:
    def test_boot_id_decides_not_mtime(self, tmp_path, monkeypatch):
        from tinyagentos import console_secret

        path = tmp_path / "s"
        monkeypatch.setenv("TAOS_CONSOLE_SECRET_FILE", str(path))
        monkeypatch.setattr(console_secret, "current_boot_id", lambda: "boot-A", raising=False)
        first = console_secret.get_secret()
        old = time.time() - 10 * 365 * 86400
        os.utime(path, (old, old))  # an NTP jump must not rotate it
        assert console_secret.get_secret() == first
        monkeypatch.setattr(console_secret, "current_boot_id", lambda: "boot-B", raising=False)
        assert console_secret.get_secret() != first

    def test_group_or_world_readable_file_is_replaced(self, tmp_path, monkeypatch):
        from tinyagentos import console_secret

        path = tmp_path / "s"
        monkeypatch.setenv("TAOS_CONSOLE_SECRET_FILE", str(path))
        monkeypatch.setattr(console_secret, "current_boot_id", lambda: "boot-A", raising=False)
        first = console_secret.get_secret()
        os.chmod(path, 0o644)
        assert console_secret.get_secret() != first


class TestLimiterClock:
    def test_a_future_stamp_cannot_lock_for_years(self):
        from tinyagentos.auth import _PinAttemptLimiter

        lim = _PinAttemptLimiter()
        lim._state["u"] = (6, time.time() + 5 * 365 * 86400)
        assert lim.retry_after("u") <= 61

    def test_failures_decay_while_quiet(self):
        from tinyagentos.auth import _PinAttemptLimiter

        lim = _PinAttemptLimiter()
        lim._state["u"] = (20, 1000.0)
        lim.record_failure("u", now=1000.0 + 60 * 3600)
        assert lim._state["u"][0] < 20


class TestIdleExpiry:
    def test_console_sessions_expire_when_idle(self, app):
        mgr = app.state.auth
        uid = mgr.get_user_by_username("owner")["id"] if hasattr(mgr, "get_user_by_username") else ""
        tok = mgr.create_session(user_id=uid, console=True)
        other = mgr.create_session(user_id=uid)
        assert mgr.validate_session(tok) is not None
        mgr._sessions[tok] = {**mgr._sessions[tok], "last_seen": time.time() - 3 * 3600}
        assert mgr.validate_session(tok) is None
        mgr._sessions[other] = {**mgr._sessions[other], "last_seen": time.time() - 3 * 3600}
        assert mgr.validate_session(other) is not None


class TestKioskWithoutATicket:
    @pytest.mark.asyncio
    async def test_the_sign_in_page_says_it_is_not_the_console(self, app):
        async with _client(app, host="192.168.1.10", header=None) as c:
            r = await c.get("/auth/login?console_missing=1")
        assert "not running as the device console" in r.text
