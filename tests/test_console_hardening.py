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
    yield
    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()


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
    async def test_sign_in_page_on_console_revokes_the_session(self, app, console):
        token = await _pin_in(console)
        await console.get("/auth/login")
        async with _client(app, host="192.168.1.10", header=None) as lan:
            r = await lan.get("/api/settings/lock", headers={"Cookie": f"taos_session={token}"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_sign_in_page_off_console_leaves_remote_sessions(self, app):
        async with _client(app, host="192.168.1.10", header=None) as lan:
            r = await lan.post("/auth/login", json={"username": "owner", "password": PASSWORD})
            assert r.status_code == 200
            await lan.get("/auth/login")
            assert (await lan.get("/api/settings/lock")).status_code == 200


# --------------------------------------------------------------------------- #
#  G19: loopback alone is not the console                                     #
# --------------------------------------------------------------------------- #

class TestConsoleSecret:
    @pytest.mark.asyncio
    async def test_the_old_constant_header_is_not_the_console(self, app):
        async with _client(app, header="1") as old:
            assert (await old.post("/auth/lock-power-menu")).status_code == 403
            r = await old.post("/auth/pin-login", json={"username": "owner", "pin": PIN})
            assert r.status_code == 404
            assert "taos_session" not in r.cookies

    @pytest.mark.asyncio
    async def test_no_header_at_all_is_not_the_console(self, app):
        async with _client(app, header=None) as bare:
            assert (await bare.post("/auth/lock-power-menu")).status_code == 403

    @pytest.mark.asyncio
    async def test_the_secret_header_is_the_console(self, console):
        r = await console.post("/auth/lock-power-menu")
        assert r.status_code == 200

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
            r = await shell.post("/auth/lock-power-menu", headers={"X-taOS-Console": "1"})
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

    def test_secret_file_is_private_and_regenerated_each_boot(self, tmp_path, monkeypatch):
        from tinyagentos import console_secret

        path = tmp_path / "fresh" / "secret"
        monkeypatch.setenv("TAOS_CONSOLE_SECRET_FILE", str(path))
        first = console_secret.get_secret()
        assert first and len(first) >= 32
        assert (path.stat().st_mode & 0o777) == 0o600
        assert console_secret.get_secret() == first
        # A file from before this boot is replaced.
        old = time.time() - 10 * 365 * 86400
        os.utime(path, (old, old))
        second = console_secret.get_secret()
        assert second and second != first

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
