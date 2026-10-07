"""S1: named scopes on device bearer tokens (see SPEC-taos-companion-device S1)."""
from __future__ import annotations

import re

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient


PLAIN = "http://testserver:6969"
TLS = "https://testserver:6974"


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _concrete(rx: re.Pattern) -> str:
    """A concrete path that the anchored regex accepts."""
    path = rx.pattern.lstrip("^").rstrip("$").replace("[^/]+", "x")
    assert rx.match(path), path
    return path


def _all_gate_cases():
    from tinyagentos.auth_middleware import (
        _DEVICE_BEARER_PATHS, _DEVICE_EXEMPT_SCOPED_PATHS,
    )
    cases = [(m, _concrete(rx), sc) for m, rx, sc in _DEVICE_BEARER_PATHS]
    cases += [(m, _concrete(rx), sc) for m, rx, sc in _DEVICE_EXEMPT_SCOPED_PATHS]
    return cases


@pytest_asyncio.fixture
async def dapp(app, client):
    """The conftest app with all stores initialised (via `client`)."""
    return app


def _client(app, base=PLAIN):
    return AsyncClient(transport=ASGITransport(app=app), base_url=base)


async def _raw_legacy(app, platform, token="taosdev_legacy_" + "a" * 20):
    """A pre-S1 row: inserted by raw SQL with the scopes column left NULL."""
    db = app.state.device_store._db
    await db.execute(
        "INSERT INTO devices (device_id, user_id, platform, push_token, scoped_token) "
        "VALUES (?, 'u1', ?, '', ?)",
        ("legacy-" + platform, platform, token),
    )
    await db.commit()
    return token


def _body(method, path=""):
    # Some routes parse the body before anything else; give them valid JSON.
    if method == "GET":
        return {}
    if path == "/api/chat/messages":
        return {"json": {"channel_id": "nope", "content": "hi"}}
    if path == "/api/device/v1/voice":
        return {"content": b"\x00\x00" * 10}
    if path.endswith("/files/upload"):
        # multipart: the route validates the file before its handler runs
        return {"files": {"file": ("a.txt", b"hi", "text/plain")}}
    return {"json": {}}


def _refused(resp) -> bool:
    return resp.status_code == 403 and (
        "device_scope_missing" in resp.text or "device_tls_required" in resp.text
    )


# ---------------------------------------------------------------- guards

def test_every_classified_path_has_a_known_scope():
    from tinyagentos.auth_middleware import _DEVICE_BEARER_PATHS, device_scope_for
    from tinyagentos.device_scopes import ALL_SCOPES, LEGACY_SCOPES
    assert len(_DEVICE_BEARER_PATHS) >= 8
    for m, rx, scope in _DEVICE_BEARER_PATHS:
        assert scope in ALL_SCOPES, (m, rx.pattern)
        assert device_scope_for(m, _concrete(rx)) == scope
        # Paths that shipped before S1 are in the legacy set; paths added after
        # (voice) deliberately are not: test_legacy_null_scope_token_gate pins
        # that a legacy token is refused on every path outside LEGACY_SCOPES.
        assert scope in LEGACY_SCOPES or scope in ("voice:stt", "voice:tts"), (m, rx.pattern)
    assert device_scope_for("GET", "/api/share/destinations") == "library:ingest"
    assert device_scope_for("GET", "/api/settings") is None
    assert device_scope_for("POST", "/api/decisions") is None


def test_scope_sets_are_literal_and_consistent():
    from tinyagentos import device_scopes as ds
    assert ds.LEGACY_SCOPES == {
        "push:register", "agents:read", "decisions:answer",
        "library:ingest", "files:upload", "chat:send",
    }
    assert ds.EMBEDDED_DEFAULT == {"agents:read", "decisions:answer"}
    assert ds.EMBEDDED_NEVER == {"library:ingest", "files:upload", "push:register"}
    assert ds.TALK == {"voice:stt", "voice:tts", "chat:send"}
    for grp in (ds.LEGACY_SCOPES, ds.EMBEDDED_DEFAULT, ds.EMBEDDED_NEVER, ds.TALK):
        assert grp <= ds.ALL_SCOPES
    assert len(ds.ALL_SCOPES) == 9


def test_tls_port_env(monkeypatch):
    from tinyagentos.device_scopes import device_tls_port
    monkeypatch.delenv("TAOS_DEVICE_TLS_PORT", raising=False)
    assert device_tls_port() == 6974
    monkeypatch.setenv("TAOS_DEVICE_TLS_PORT", "7443")
    assert device_tls_port() == 7443
    monkeypatch.setenv("TAOS_DEVICE_TLS_PORT", "junk")
    assert device_tls_port() == 6974


@pytest.mark.parametrize("platform", ["embedded", "wearos", "linux", "", "IOS"])
def test_null_scopes_on_non_legacy_platform_is_empty(platform):
    from tinyagentos.device_scopes import effective_scopes
    assert effective_scopes({"platform": platform, "scopes": None}) == frozenset()
    assert effective_scopes({"platform": platform}) == frozenset()


@pytest.mark.parametrize("platform", ["ios", "watchos", "android"])
def test_null_scopes_on_legacy_platform_is_legacy_set(platform):
    from tinyagentos.device_scopes import LEGACY_SCOPES, effective_scopes
    assert effective_scopes({"platform": platform, "scopes": None}) == LEGACY_SCOPES


def test_effective_scopes_filters_unknown_and_embedded_never():
    from tinyagentos.device_scopes import effective_scopes
    assert effective_scopes({"platform": "ios", "scopes": "agents:read bogus"}) == {"agents:read"}
    assert effective_scopes({"platform": "ios", "scopes": ""}) == frozenset()
    assert effective_scopes(
        {"platform": "embedded", "scopes": "agents:read library:ingest files:upload push:register"}
    ) == {"agents:read"}


# ---------------------------------------------------------------- store

@pytest.mark.asyncio
async def test_register_stores_explicit_scopes(dapp):
    from tinyagentos.device_scopes import EMBEDDED_DEFAULT, LEGACY_SCOPES
    st = dapp.state.device_store
    for plat in ("ios", "watchos", "android", "wearos"):
        d = await st.register(user_id="u", platform=plat)
        assert d["scopes"] is not None
        assert set(d["scopes"].split()) == LEGACY_SCOPES
    e = await st.register(user_id="u", platform="embedded")
    assert e["scopes"] == "agents:read decisions:answer"
    assert set(e["scopes"].split()) == EMBEDDED_DEFAULT
    other = await st.register(user_id="u", platform="linux")
    assert other["scopes"] == ""


@pytest.mark.asyncio
async def test_set_scopes_validates(dapp):
    st = dapp.state.device_store
    d = await st.register(user_id="u", platform="ios")
    with pytest.raises(ValueError):
        await st.set_scopes(d["device_id"], ["agents:read", "bogus"])
    with pytest.raises(ValueError):
        await st.set_scopes(d["device_id"], "agents:read")  # a bare string
    assert (await st.get(d["device_id"]))["scopes"] == d["scopes"]  # unchanged
    got = await st.set_scopes(d["device_id"], ["voice:tts", "agents:read"])
    assert got["scopes"] == "agents:read voice:tts"


@pytest.mark.asyncio
async def test_migration_adds_nullable_column(tmp_path):
    import aiosqlite
    from tinyagentos.device_store import DeviceStore
    path = tmp_path / "old.db"
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            "CREATE TABLE devices (device_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,"
            " platform TEXT NOT NULL, push_token TEXT NOT NULL DEFAULT '',"
            " scoped_token TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL DEFAULT '',"
            " registered_at INTEGER NOT NULL DEFAULT 0, last_seen INTEGER NOT NULL DEFAULT 0,"
            " revoked INTEGER NOT NULL DEFAULT 0, blocked INTEGER NOT NULL DEFAULT 0);"
            "INSERT INTO devices (device_id,user_id,platform,scoped_token) VALUES ('a','u','ios','t');"
        )
        await db.commit()
    st = DeviceStore(path)
    await st.init()
    try:
        assert (await st.get("a"))["scopes"] is None  # no backfill: NULL stays the marker
    finally:
        await st.close()


# ---------------------------------------------------------------- legacy gate

@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["ios", "watchos", "android"])
@pytest.mark.parametrize("method,path,scope", _all_gate_cases())
async def test_legacy_null_scope_token_gate(dapp, platform, method, path, scope, monkeypatch):
    """A NULL-scope legacy token reaches every path whose scope is in LEGACY_SCOPES
    and is refused 403 device_scope_missing on every other classified path
    (voice, added after S1: legacy tokens never silently gain new scopes)."""
    from tinyagentos.device_scopes import LEGACY_SCOPES

    print(f"DEBUG: path={path!r}, method={method!r}")
    if path == "/api/device/v1/events" and method == "GET":
        print("DEBUG: Setting mock for events")
        async def mock_events_stream(*args, **kwargs):
            print("DEBUG: Mock called, yielding")
            yield b": ping\n\n"
            print("DEBUG: Mock done")
        monkeypatch.setattr("tinyagentos.routes.device_state._events_stream", mock_events_stream)

    tok = await _raw_legacy(dapp, platform)
    async with _client(dapp) as c:
        r = await c.request(method, path, headers=_bearer(tok), **_body(method, path))
    assert r.status_code != 401, (path, r.text)
    if scope in LEGACY_SCOPES:
        assert not _refused(r), (path, r.text)
    else:
        assert r.status_code == 403, (path, r.text)
        assert r.json()["detail"] == {"error": "device_scope_missing", "scope": scope}, path


@pytest.mark.asyncio
async def test_unclassified_path_is_refused_by_the_resolver(dapp):
    """A route that calls require_device on a path nobody classified is refused."""
    from tinyagentos.device_auth import require_device
    dev = await dapp.state.device_store.register(user_id="u", platform="ios")
    app2 = FastAPI()
    app2.state.device_store = dapp.state.device_store

    @app2.get("/unclassified")
    async def _r(d=Depends(require_device)):
        return {"ok": True}

    async with _client(app2) as c:
        r = await c.get("/unclassified", headers=_bearer(dev["scoped_token"]))
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "device_scope_missing"


# ---------------------------------------------------------------- embedded

EMBEDDED_REFUSED = [
    ("POST", "/api/library/ingest", "library:ingest"),
    ("POST", "/api/projects/x/files/upload", "files:upload"),
    ("PATCH", "/api/devices/x/push-token", "push:register"),
    ("GET", "/api/share/destinations", "library:ingest"),
    ("POST", "/api/chat/messages", "chat:send"),
]
EMBEDDED_ALLOWED = [
    ("GET", "/api/decisions"),
    ("GET", "/api/decisions/x"),
    ("POST", "/api/decisions/x/answer"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,scope", EMBEDDED_REFUSED)
async def test_embedded_refused_on_tls_without_scope(dapp, method, path, scope):
    d = await dapp.state.device_store.register(user_id="u1", platform="embedded")
    async with _client(dapp, TLS) as c:
        r = await c.request(method, path, headers=_bearer(d["scoped_token"]), **_body(method, path))
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail == {"error": "device_scope_missing", "scope": scope}


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", EMBEDDED_ALLOWED)
async def test_embedded_allowed_past_gate_on_tls(dapp, method, path):
    d = await dapp.state.device_store.register(user_id="u1", platform="embedded")
    async with _client(dapp, TLS) as c:
        r = await c.request(method, path, headers=_bearer(d["scoped_token"]), **_body(method, path))
    assert r.status_code != 401 and not _refused(r), r.text


@pytest.mark.asyncio
async def test_embedded_scope_server_reflects_tls_port(dapp):
    """The https/6974 check is meaningful: the ASGI scope carries it."""
    seen = {}

    async def spy(scope, receive, send):
        seen.update(scheme=scope["scheme"], server=scope["server"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async with AsyncClient(transport=ASGITransport(app=spy), base_url=TLS) as c:
        await c.get("/x")
    assert seen["scheme"] == "https" and seen["server"][1] == 6974


@pytest.mark.asyncio
async def test_embedded_refused_on_plain_http(dapp):
    d = await dapp.state.device_store.register(user_id="u1", platform="embedded")
    async with _client(dapp, PLAIN) as c:
        r = await c.get("/api/decisions", headers=_bearer(d["scoped_token"]))
    assert r.status_code == 403
    assert r.json()["detail"] == {"error": "device_tls_required"}


@pytest.mark.asyncio
@pytest.mark.parametrize("base", [
    "http://testserver:6974",    # right port, no TLS
    "https://testserver:6969",   # TLS, wrong port
    "https://testserver",        # TLS, default 443
])
async def test_embedded_requires_both_https_and_the_port(dapp, base):
    d = await dapp.state.device_store.register(user_id="u1", platform="embedded")
    async with _client(dapp, base) as c:
        r = await c.get("/api/decisions", headers=_bearer(d["scoped_token"]))
    assert r.status_code == 403 and "device_tls_required" in r.text


@pytest.mark.asyncio
async def test_forwarded_headers_do_not_satisfy_tls(dapp):
    d = await dapp.state.device_store.register(user_id="u1", platform="embedded")
    hdrs = {**_bearer(d["scoped_token"]), "X-Forwarded-Proto": "https",
            "X-Forwarded-Port": "6974", "Forwarded": "proto=https"}
    async with _client(dapp, PLAIN) as c:
        r = await c.get("/api/decisions", headers=hdrs)
    assert r.status_code == 403 and "device_tls_required" in r.text


@pytest.mark.asyncio
async def test_embedded_never_scopes_survive_set_scopes(dapp):
    st = dapp.state.device_store
    d = await st.register(user_id="u1", platform="embedded")
    await st.set_scopes(d["device_id"], ["agents:read", "library:ingest"])  # storable...
    async with _client(dapp, TLS) as c:
        r = await c.post("/api/library/ingest", headers=_bearer(d["scoped_token"]),
                         data={"url": "https://example.com/x"})
    assert r.status_code == 403  # ...but refused at enforcement
    assert r.json()["detail"]["scope"] == "library:ingest"


@pytest.mark.asyncio
async def test_embedded_talk_grants_chat_send(dapp):
    from tinyagentos.device_scopes import EMBEDDED_DEFAULT, TALK
    st = dapp.state.device_store
    d = await st.register(user_id="u1", platform="embedded")
    await st.set_scopes(d["device_id"], EMBEDDED_DEFAULT | TALK)
    async with _client(dapp, TLS) as c:
        r = await c.post("/api/chat/messages", headers=_bearer(d["scoped_token"]),
                         json={"channel_id": "nope", "content": "hi"})
    assert not _refused(r) and r.status_code != 401, r.text


# ---------------------------------------------------------------- refusal side effects

@pytest.mark.asyncio
async def test_refusal_does_not_touch_last_seen(dapp):
    st = dapp.state.device_store
    d = await st.register(user_id="u1", platform="embedded")
    await st._db.execute("UPDATE devices SET last_seen = 1 WHERE device_id = ?", (d["device_id"],))
    await st._db.commit()
    async with _client(dapp, TLS) as c:
        r = await c.post("/api/library/ingest", headers=_bearer(d["scoped_token"]))
    assert r.status_code == 403
    assert (await st.get(d["device_id"]))["last_seen"] == 1


# ---------------------------------------------------------------- device_scope()

def test_device_scope_rejects_unknown_at_definition():
    from tinyagentos.device_auth import device_scope
    with pytest.raises(ValueError):
        device_scope("bogus")
    device_scope("voice:tts")


@pytest.mark.asyncio
async def test_device_scope_dependency(dapp):
    from tinyagentos.device_auth import device_scope
    st = dapp.state.device_store
    app2 = FastAPI()
    app2.state.device_store = st

    @app2.post("/api/device/v1/_probe")
    async def _probe(d=Depends(device_scope("voice:tts"))):
        return {"id": d["device_id"]}

    d = await st.register(user_id="u1", platform="ios")  # LEGACY: no voice:tts
    async with _client(app2) as c:
        r = await c.post("/api/device/v1/_probe", headers=_bearer(d["scoped_token"]))
        assert r.status_code == 403
        assert r.json()["detail"] == {"error": "device_scope_missing", "scope": "voice:tts"}
        await st.set_scopes(d["device_id"], ["voice:tts"])
        r = await c.post("/api/device/v1/_probe", headers=_bearer(d["scoped_token"]))
        assert r.status_code == 200
        r = await c.post("/api/device/v1/_probe")
        assert r.status_code == 401


# ---------------------------------------------------------------- pairing + platforms

@pytest.mark.asyncio
async def test_register_ios_and_wearos_store_legacy_set(dapp, tmp_path):
    from tinyagentos.device_scopes import LEGACY_SCOPES
    uid = dapp.state.auth.find_user("admin")["id"] if dapp.state.auth.find_user("admin") else "u1"
    st = dapp.state.device_store
    for plat in ("ios", "wearos"):
        d = await st.register(user_id=uid, platform=plat)
        assert set((await st.get(d["device_id"]))["scopes"].split()) == LEGACY_SCOPES


@pytest.mark.asyncio
async def test_pair_request_platforms(client):
    for plat in ("embedded", "wearos", "ios", "watchos", "android"):
        r = await client.post("/api/devices/pair-requests", json={"platform": plat})
        assert r.status_code in (200, 201), (plat, r.text)
    r = await client.post("/api/devices/pair-requests", json={"platform": "linux"})
    assert r.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("plat", ["embedded", "wearos"])
async def test_pair_request_push_token_rejected(client, plat):
    r = await client.post("/api/devices/pair-requests",
                          json={"platform": plat, "push_token": "https://up.example/x"})
    assert r.status_code == 400
    r = await client.post("/api/devices/pair-requests", json={"platform": plat, "push_token": ""})
    assert r.status_code in (200, 201)


@pytest.mark.asyncio
async def test_register_route_wearos_and_embedded(client, app):
    r = await client.post("/api/devices/register", json={"platform": "wearos"})
    assert r.status_code == 200, r.text
    assert set(r.json()["scopes"].split()) == {
        "push:register", "agents:read", "decisions:answer",
        "library:ingest", "files:upload", "chat:send"}
    r = await client.post("/api/devices/register",
                          json={"platform": "wearos", "push_token": "https://up.example/x"})
    assert r.status_code == 400
    r = await client.post("/api/devices/register", json={"platform": "embedded"})
    assert r.status_code == 422  # embedded pairs only via the Decision flow


@pytest.mark.asyncio
async def test_pairing_approval_mints_embedded_with_default_scopes(client, app):
    """The Decision-flow mint (decisions.py) stores the explicit embedded set."""
    r = await client.post("/api/devices/pair-requests", json={"platform": "embedded"})
    pid = r.json()["pair_request_id"]
    decs = (await client.get("/api/decisions")).json()
    items = decs["items"] if isinstance(decs, dict) else decs
    dec = next(d for d in items if (d.get("metadata") or {}).get("pair_request_id") == pid)
    r = await client.post(f"/api/decisions/{dec['id']}/answer", json={"value": "approve"})
    assert r.status_code == 200, r.text
    rec = await app.state.device_pair_requests.get(pid)
    dev = await app.state.device_store.get(rec["device_id"])
    assert dev["platform"] == "embedded"
    assert dev["scopes"] == "agents:read decisions:answer"


# ---------------------------------------------------------------- push fan-out

@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["embedded", "wearos"])
async def test_push_fanout_skips_embedded_and_wearos(platform):
    from tinyagentos.notifications_push import _send_one_device
    from tinyagentos.push import send_device_push

    class Boom:
        async def send(self, *a, **k):
            raise AssertionError("must not be called")

    dev = {"device_id": "d", "platform": platform, "push_token": "something"}
    out = await _send_one_device(
        dev, {"title": "t", "body": "b"}, None, Boom(), Boom(), device_store=None)
    assert out == "skipped"
    assert await send_device_push(dev, {}, apns_sender=Boom(), up_sender=Boom()) is False
