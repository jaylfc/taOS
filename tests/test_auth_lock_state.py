"""GET /auth/lock-state: is the handset's own screen locked right now.

Called by a device-side service on the same machine with no cookie. The caller
treats anything but 200 + {"locked": false} as locked, so these tests measure
the refusing directions first and the single unlocked answer last.
"""
from __future__ import annotations

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

    auth_routes._pin_limiter = type(auth_routes._pin_limiter)()
    auth_routes._CONSOLE_LOCKED = True
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
    assert r.json() == {"locked": True}
    assert r.headers["cache-control"] == "no-store"


def test_route_is_exempt_from_the_session_gate():
    assert "/auth/lock-state" in EXEMPT_PATHS


@pytest.mark.asyncio
async def test_console_pin_unlock_then_lock(app, console):
    r = await console.post("/auth/pin-login", json={"pin": PIN})
    assert r.status_code == 200
    r = await _state(console)
    assert r.json() == {"locked": False}
    assert set(r.json()) == {"locked"}
    token = console.cookies.get("taos_session")
    csrf = bound_csrf_token(app, token)
    console.cookies.clear()
    console.cookies.set("taos_session", token)
    console.cookies.set("csrf_token", csrf)
    r = await console.post("/auth/lock", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert (await _state(console)).json() == {"locked": True}


@pytest.mark.asyncio
async def test_console_password_login_unlocks(console):
    r = await console.post("/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    assert (await _state(console)).json() == {"locked": False}


@pytest.mark.asyncio
async def test_failed_pin_leaves_it_locked(console):
    r = await console.post("/auth/pin-login", json={"pin": "0000"})
    assert r.status_code == 401
    r = await console.post("/auth/login", json={"password": "wrong"})
    assert r.status_code == 401
    assert (await _state(console)).json() == {"locked": True}


@pytest.mark.asyncio
async def test_remote_unlock_does_not_flip_it(app, console, lan):
    r = await lan.post("/auth/login", json={"password": PASSWORD})
    assert r.status_code == 200
    r = await lan.post("/auth/pin-login", json={"pin": PIN})
    assert r.status_code == 404
    assert (await _state(console)).json() == {"locked": True}


@pytest.mark.asyncio
async def test_remote_lock_does_not_relock_an_unlocked_console(app, console, lan):
    await console.post("/auth/pin-login", json={"pin": PIN})
    await lan.post("/auth/lock")
    assert (await _state(console)).json() == {"locked": False}


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
