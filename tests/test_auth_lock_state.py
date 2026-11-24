"""GET /auth/lock-state: is the handset's own screen locked right now.

Called by a device-side service on the same machine with no cookie. The caller
treats anything but 200 + {"locked": false} as locked, so these tests measure
the refusing directions first and the single unlocked answer last.
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import bound_csrf_token

from tinyagentos.auth import AuthManager
from tinyagentos.auth_middleware import EXEMPT_PATHS

PASSWORD = "correct horse battery staple"
PIN = "4913"
UA = "Mozilla/5.0 (Linux; taOS kiosk) Chromium/131"


@pytest.fixture(autouse=True)
def _fresh_state():
    from tinyagentos.routes import auth as auth_routes

    assert isinstance(auth_routes._CONSOLE_SESSION, int)
    assert auth_routes._CONSOLE_SESSION >= 1_700_000_000
    # Earlier tests in the same second may have incremented it past the clock
    # (each lock transition adds one); only a wild value is a failure here.
    assert auth_routes._CONSOLE_SESSION <= int(time.time()) + 10_000

    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    auth_routes._CONSOLE_LOCKED = True
    auth_routes._CONSOLE_SESSION = int(time.time())
    yield
    auth_routes._CONSOLE_LOCKED = True


@pytest.fixture()
def app(tmp_path, monkeypatch):
    from tinyagentos.app import create_app

    monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
    application = create_app(data_dir=tmp_path)
    mgr = AuthManager(tmp_path)
    mgr.setup_user("owner", "Device Owner", "", PASSWORD)
    mgr.set_pin("owner", PIN)
    application.state.auth = mgr
    return application


def _client(app, host, console_header=True):
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
async def lan(app):
    async with _client(app, "192.168.1.10") as c:
        yield c


async def _state(client, **kw):
    return await client.get("/auth/lock-state", **kw)


@pytest.mark.asyncio
async def test_locked_at_startup(console):
    r = await _state(console)
    assert r.status_code == 200
    assert r.json()["locked"] is True
    assert r.headers["cache-control"] == "no-store"


def test_route_is_exempt_from_the_session_gate():
    assert "/auth/lock-state" in EXEMPT_PATHS


@pytest.mark.asyncio
async def test_console_pin_unlock_then_lock(app, console):
    r = await console.post("/auth/pin-login", json={"pin": PIN})
    assert r.status_code == 200
    r = await _state(console)
    assert r.json()["locked"] is False
    assert set(r.json()) == {"locked", "session"}
    token = console.cookies.get("taos_session")
    csrf = bound_csrf_token(app, token)
    console.cookies.clear()
    console.cookies.set("taos_session", token)
    console.cookies.set("csrf_token", csrf)
    r = await console.post("/auth/lock", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert (await _state(console)).json()["locked"] is True


@pytest.mark.asyncio
async def test_console_password_login_unlocks(console):
    r = await console.post("/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    assert (await _state(console)).json()["locked"] is False


@pytest.mark.asyncio
async def test_failed_pin_leaves_it_locked(console):
    r = await console.post("/auth/pin-login", json={"pin": "0000"})
    assert r.status_code == 401
    r = await console.post("/auth/login", json={"password": "wrong"})
    assert r.status_code == 401
    assert (await _state(console)).json()["locked"] is True


@pytest.mark.asyncio
async def test_remote_unlock_does_not_flip_it(app, console, lan):
    r = await lan.post("/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    r = await lan.post("/auth/pin-login", json={"pin": PIN})
    assert r.status_code == 404
    assert (await _state(console)).json()["locked"] is True


@pytest.mark.asyncio
async def test_remote_lock_does_not_relock_an_unlocked_console(app, console, lan):
    await console.post("/auth/pin-login", json={"pin": PIN})
    await lan.post("/auth/lock")
    assert (await _state(console)).json()["locked"] is False


@pytest.mark.asyncio
async def test_non_loopback_peer_gets_404(lan):
    r = await _state(lan)
    assert r.status_code == 404
    assert "locked" not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
async def test_forwarding_header_gets_404(console, header):
    r = await _state(console, headers={header: "192.168.1.10"})
    assert r.status_code == 404
    assert "locked" not in r.text


@pytest.mark.asyncio
async def test_missing_console_header_gets_404(app):
    async with _client(app, "127.0.0.1", console_header=False) as c:
        r = await _state(c)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_console_login_page_after_session_loss_relocks(app, console):
    """Session ended without /auth/lock (expiry, revoked elsewhere): the console
    then lands on the sign-in page, which is what locked means."""
    await console.post("/auth/pin-login", json={"pin": PIN})
    assert (await _state(console)).json()["locked"] is False
    app.state.auth.revoke_session(console.cookies.get("taos_session"))
    console.cookies.clear()
    r = await console.get("/auth/login")
    assert r.status_code == 200
    assert (await _state(console)).json()["locked"] is True


@pytest.mark.asyncio
async def test_console_status_probe_without_session_relocks(app, console):
    """The SPA's LoginGate never GETs /auth/login: /auth/status is its signal."""
    await console.post("/auth/pin-login", json={"pin": PIN})
    console.cookies.clear()
    await console.get("/auth/status")
    assert (await _state(console)).json()["locked"] is True


@pytest.mark.asyncio
async def test_remote_login_page_leaves_it_unlocked(app, console, lan):
    await console.post("/auth/pin-login", json={"pin": PIN})
    await lan.get("/auth/login")
    await lan.get("/auth/status")
    assert (await _state(console)).json()["locked"] is False


# ---- lock-session number -------------------------------------------------


async def _body(client):
    r = await _state(client)
    assert r.status_code == 200
    return r.json()


async def _pin_unlock(client):
    r = await client.post("/auth/pin-login", json={"pin": PIN})
    assert r.status_code == 200


async def _console_lock(app, client):
    token = client.cookies.get("taos_session")
    csrf = bound_csrf_token(app, token)
    client.cookies.clear()
    client.cookies.set("taos_session", token)
    client.cookies.set("csrf_token", csrf)
    r = await client.post("/auth/lock", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    return csrf


def test_session_seeded_from_time_at_import():
    from tinyagentos.routes import auth as auth_routes

    assert isinstance(auth_routes._CONSOLE_SESSION, int)
    assert auth_routes._CONSOLE_SESSION >= 1_700_000_000
    assert auth_routes._CONSOLE_SESSION <= int(time.time())


@pytest.mark.asyncio
async def test_body_has_exactly_locked_and_session(console, app):
    before = int(time.time())
    body = await _body(console)
    assert set(body) == {"locked", "session"}
    assert body["locked"] is True
    assert type(body["session"]) is int
    assert body["session"] >= before - 5


@pytest.mark.asyncio
async def test_unlock_keeps_number_and_relock_adds_one(app, console):
    n = (await _body(console))["session"]
    await _pin_unlock(console)
    body = await _body(console)
    assert body == {"locked": False, "session": n}
    await _console_lock(app, console)
    body = await _body(console)
    assert body == {"locked": True, "session": n + 1}


@pytest.mark.asyncio
async def test_repeat_lock_signals_while_locked_do_not_change_it(app, console):
    await _pin_unlock(console)
    csrf = await _console_lock(app, console)
    n = (await _body(console))["session"]
    r = await console.post("/auth/lock", headers={"X-CSRF-Token": csrf})
    for _ in range(3):
        console.cookies.clear()
        await console.get("/auth/status")
    await console.get("/auth/login")
    assert (await _body(console)) == {"locked": True, "session": n}


@pytest.mark.asyncio
async def test_remote_lock_and_page_load_do_not_change_it(app, console, lan):
    await _pin_unlock(console)
    n = (await _body(console))["session"]
    await lan.post("/auth/lock")
    await lan.get("/auth/login")
    await lan.get("/auth/status")
    assert (await _body(console)) == {"locked": False, "session": n}
    await _console_lock(app, console)
    m = (await _body(console))["session"]
    await lan.get("/auth/login")
    await lan.get("/auth/status")
    assert (await _body(console)) == {"locked": True, "session": m}


@pytest.mark.asyncio
async def test_unlock_lock_unlock_lock_is_plus_two(app, console):
    n = (await _body(console))["session"]
    await _pin_unlock(console)
    await _console_lock(app, console)
    console.cookies.clear()
    await _pin_unlock(console)
    await _console_lock(app, console)
    assert (await _body(console)) == {"locked": True, "session": n + 2}


@pytest.mark.asyncio
async def test_sign_in_surface_relock_increments_once(app, console):
    await _pin_unlock(console)
    n = (await _body(console))["session"]
    console.cookies.clear()
    await console.get("/auth/status")
    await console.get("/auth/status")
    assert (await _body(console)) == {"locked": True, "session": n + 1}


@pytest.mark.asyncio
async def test_refusals_leak_no_session(console, lan):
    r = await _state(lan)
    assert r.status_code == 404 and "session" not in r.text
    r = await _state(console, headers={"X-Forwarded-For": "192.168.1.10"})
    assert r.status_code == 404 and "session" not in r.text
