"""Cast tokens: the security boundary of the screencast media path (tsk-vecfxo).

A TV fetches the cast media URL ITSELF, with no taOS session, cookie or bearer.
So the media route sits outside the session gate and is gated solely by a cast
token. These tests are built on the token, never on the happy path: "the cast
URL renders the view" passes on a completely unauthenticated route and cannot
fail on the defect that matters.

Every media fetch below goes through ``tv``, a client with NO session cookie,
exactly like a Chromecast or Apple TV on the LAN.
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def canvas_ready(client):
    """Init the project canvas store (lifespan-owned) on the shared projects.db."""
    app = client._transport.app
    store = app.state.project_canvas_store
    if store._db is not None:
        await store.close()
    store.db_path = app.state.project_store.db_path
    await store.init()
    yield app
    await store.close()


@pytest_asyncio.fixture
async def tv(canvas_ready):
    """An unauthenticated LAN device: no cookie, no bearer, no CSRF header."""
    async with AsyncClient(
        transport=ASGITransport(app=canvas_ready), base_url="http://test",
    ) as c:
        yield c


async def _project(app, slug: str) -> str:
    owner = app.state.auth.find_user("admin")["id"]
    p = await app.state.project_store.create_project(
        name=slug, slug=slug, created_by="user", user_id=owner,
    )
    return p["id"]


async def _start_cast(client, project_id: str, device_id: str = "tv-living-room", **extra):
    body = {"view": f"canvas:{project_id}", "device_id": device_id, "mode": "still"}
    body.update(extra)
    resp = await client.post("/api/cast/sessions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _media(project_id: str, token: str) -> str:
    return f"/cast/media/canvas/{project_id}/still.png?token={token}"


@pytest.mark.asyncio
async def test_valid_cast_token_serves_still_image(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-ok")
    cast = await _start_cast(client, pid)
    resp = await tv.get(_media(pid, cast["token"]))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "no-store" in resp.headers.get("cache-control", "")


@pytest.mark.asyncio
async def test_missing_cast_token_refused(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-missing")
    await _start_cast(client, pid)
    resp = await tv.get(f"/cast/media/canvas/{pid}/still.png")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_cast_token_refused(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-expired")
    cast = await _start_cast(client, pid)
    store = canvas_ready.state.cast_tokens
    real = time.time()
    store.clock = lambda: real + store.ttl_seconds + 1
    resp = await tv.get(_media(pid, cast["token"]))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_extends_an_active_cast(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-refresh")
    cast = await _start_cast(client, pid)
    store = canvas_ready.state.cast_tokens
    real = time.time()
    store.clock = lambda: real + store.ttl_seconds - 5
    r = await client.post(f"/api/cast/sessions/{cast['cast_id']}/refresh")
    assert r.status_code == 200, r.text
    store.clock = lambda: real + store.ttl_seconds + 5
    resp = await tv.get(_media(pid, cast["token"]))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_expired_cast_cannot_be_refreshed(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-norevive")
    cast = await _start_cast(client, pid)
    store = canvas_ready.state.cast_tokens
    real = time.time()
    store.clock = lambda: real + store.ttl_seconds + 1
    r = await client.post(f"/api/cast/sessions/{cast['cast_id']}/refresh")
    assert r.status_code == 409
    assert (await tv.get(_media(pid, cast["token"]))).status_code == 401


@pytest.mark.asyncio
async def test_token_for_view_a_cannot_fetch_view_b(client, canvas_ready, tv):
    pid_a = await _project(canvas_ready, "cast-view-a")
    pid_b = await _project(canvas_ready, "cast-view-b")
    cast_a = await _start_cast(client, pid_a)
    resp = await tv.get(_media(pid_b, cast_a["token"]))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_token_revoked_on_cast_end(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-end")
    cast = await _start_cast(client, pid)
    assert (await tv.get(_media(pid, cast["token"]))).status_code == 200
    r = await client.delete(f"/api/cast/sessions/{cast['cast_id']}")
    assert r.status_code == 200, r.text
    resp = await tv.get(_media(pid, cast["token"]))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_token_revoked_on_unpair(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-unpair")
    cast = await _start_cast(client, pid, device_id="tv-bedroom")
    other = await _start_cast(client, pid, device_id="tv-kitchen")
    assert (await tv.get(_media(pid, cast["token"]))).status_code == 200
    # The hook C1's unpair route calls. No cast-end happened.
    revoked = canvas_ready.state.cast_tokens.revoke_for_device("tv-bedroom")
    assert revoked == 1
    resp = await tv.get(_media(pid, cast["token"]))
    assert resp.status_code == 401
    # Unpairing one device must not kill another device's cast.
    assert (await tv.get(_media(pid, other["token"]))).status_code == 200


@pytest.mark.asyncio
async def test_cast_token_is_not_a_session(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-not-session")
    cast = await _start_cast(client, pid)
    token = cast["token"]
    # Not a session cookie.
    async with AsyncClient(
        transport=ASGITransport(app=canvas_ready), base_url="http://test",
        cookies={"taos_session": token},
    ) as as_cookie:
        r = await as_cookie.get("/api/projects")
        assert r.status_code == 401
    # Not a bearer credential.
    r = await tv.get("/api/projects", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    # And the reverse: a real session is not a cast token.
    r = await client.get(f"/cast/media/canvas/{pid}/still.png")
    assert r.status_code == 401
    # The token is stored only as a hash, never in plaintext.
    store = canvas_ready.state.cast_tokens
    assert token not in repr(store._by_hash)


@pytest.mark.asyncio
async def test_device_address_binding(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-bound")
    # ASGITransport presents the client as 127.0.0.1.
    bound_elsewhere = await _start_cast(client, pid, device_address="192.0.2.10")
    assert (await tv.get(_media(pid, bound_elsewhere["token"]))).status_code == 403
    bound_here = await _start_cast(client, pid, device_address="127.0.0.1")
    assert (await tv.get(_media(pid, bound_here["token"]))).status_code == 200


@pytest.mark.asyncio
async def test_live_path_is_token_gated_and_not_built(client, canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-live")
    cast = await _start_cast(client, pid)
    r = await tv.get(f"/cast/media/canvas/{pid}/live.m3u8")
    assert r.status_code == 401
    r = await tv.get(f"/cast/media/canvas/{pid}/live.m3u8?token={cast['token']}")
    assert r.status_code == 501


@pytest.mark.asyncio
async def test_cast_start_requires_a_session(canvas_ready, tv):
    pid = await _project(canvas_ready, "cast-noauth")
    r = await tv.post(
        "/api/cast/sessions",
        json={"view": f"canvas:{pid}", "device_id": "tv", "mode": "still"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_cast_start_refuses_unknown_view(client, canvas_ready):
    r = await client.post(
        "/api/cast/sessions",
        json={"view": "canvas:does-not-exist", "device_id": "tv", "mode": "still"},
    )
    assert r.status_code == 404
    r = await client.post(
        "/api/cast/sessions",
        json={"view": "desktop:anything", "device_id": "tv", "mode": "still"},
    )
    assert r.status_code == 400


def test_cast_media_exemption_is_one_prefix_read_only():
    from tinyagentos.auth_middleware import _is_exempt

    assert _is_exempt("GET", "/cast/media/canvas/p1/still.png") is True
    assert _is_exempt("HEAD", "/cast/media/canvas/p1/still.png") is True
    assert _is_exempt("POST", "/cast/media/canvas/p1/still.png") is False
    assert _is_exempt("DELETE", "/cast/media/canvas/p1/still.png") is False
    assert _is_exempt("GET", "/cast/other") is False
    assert _is_exempt("POST", "/api/cast/sessions") is False
    assert _is_exempt("GET", "/api/cast/sessions") is False
