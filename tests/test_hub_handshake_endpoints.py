"""Regression tests for tsk-ibcysh: hub friend-accept keeps stored endpoints
and filters first-link endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks

_PEER_FP = "9a2db2e23f1504cd056606553ac049c5e718e8f9ce9233876df1a7a1821af885"
_PEER_USERNAME = "remotepeer"
_PEER_SIGNING_PUB = "ab" * 32
_PEER_ENCRYPTION_PUB = "cd" * 32


def _fake_dir_resp(status=200, body=None):
    if body is None:
        body = {}
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def _patch_account_proxy(monkeypatch, handler):
    _UPSTREAM = "https://taos.my"
    orig = httpx.AsyncClient.request

    async def routed(self, method, url, **kw):
        url_s = str(url)
        if url_s.startswith(_UPSTREAM):
            return await handler(method, url_s, **kw)
        return await orig(self, method, url, **kw)

    monkeypatch.setattr("httpx.AsyncClient.request", routed)


@pytest_asyncio.fixture
async def app_with_contacts(tmp_data_dir, monkeypatch):
    from tinyagentos.app import create_app

    _app = create_app(data_dir=tmp_data_dir)
    store = _app.state.contacts_store
    if store._db is not None:
        await store.close()
    await store.init()

    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))

    def _bootstrap_hub_identity(data_dir, username="localnode"):
        import sqlite3, time
        from tinyagentos.hub import identity as _hub_identity
        hub_dir = data_dir / "hub"
        hub_dir.mkdir(parents=True, exist_ok=True)
        _hub_identity.clear()
        ident = _hub_identity.load_or_create()
        fp = _hub_identity.signing_fingerprint()
        hub_db = hub_dir / "hub.db"
        conn = sqlite3.connect(str(hub_db))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS hub_authors (
                fingerprint TEXT PRIMARY KEY, username TEXT, signing_pubkey TEXT,
                encryption_pubkey TEXT, updated_at REAL
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO hub_authors (fingerprint, username, signing_pubkey, encryption_pubkey, updated_at) VALUES (?,?,?,?,?)",
            (fp, username, ident["signing_public"], ident["encryption_public"], time.time()),
        )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS hub_objects (hash TEXT PRIMARY KEY, author TEXT NOT NULL, type TEXT NOT NULL, seq INTEGER, version INTEGER, body TEXT NOT NULL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS hub_relationships (peer TEXT NOT NULL, kind TEXT NOT NULL, statement TEXT, quota_hint INTEGER, updated_at REAL NOT NULL, PRIMARY KEY (peer, kind));
            CREATE TABLE IF NOT EXISTS hub_chain (author TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL, prev_hash TEXT, type TEXT NOT NULL, target TEXT, created_at REAL NOT NULL, PRIMARY KEY (author, seq));
            """
        )
        conn.commit()
        conn.close()
        return f"hub:{username}"

    _bootstrap_hub_identity(tmp_data_dir)

    yield _app
    store = _app.state.contacts_store
    if store is not None and store._db is not None:
        await store.close()


@pytest_asyncio.fixture
async def client_with_contacts(app_with_contacts):
    _app = app_with_contacts
    _app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = _app.state.auth.find_user("admin")
    _uid = _rec["id"] if _rec else ""
    _token = _app.state.auth.create_session(user_id=_uid, long_lived=True)
    _app.state._startup_complete = True
    transport = ASGITransport(app=_app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": _token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_repeat_friend_accept_keeps_stored_endpoints(client_with_contacts, app_with_contacts, monkeypatch):
    dir_resp_body = {
        "peer": _PEER_FP,
        "username": _PEER_USERNAME,
        "display_name": "Remote Peer",
        "signing_pubkey": _PEER_SIGNING_PUB,
        "encryption_pubkey": _PEER_ENCRYPTION_PUB,
        "endpoints": ["https://first.example.com:6969"],
    }

    async def handler(method, url, **kw):
        return _fake_dir_resp(body=dir_resp_body)

    _patch_account_proxy(monkeypatch, handler)

    resp = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-1/accept", json={"peer_fingerprint": _PEER_FP}
    )
    assert resp.status_code == 200

    store = app_with_contacts.state.contacts_store
    first_link = await store.get_peer_link(f"hub:{_PEER_FP}")
    assert first_link is not None

    # second accept with different endpoints
    dir_resp_body["endpoints"] = ["https://second.example.com:6969"]
    resp2 = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-2/accept", json={"peer_fingerprint": _PEER_FP}
    )
    assert resp2.status_code == 200

    link = await store.get_peer_link(f"hub:{_PEER_FP}")
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://first.example.com:6969"], f"kept wrong: {urls}"


@pytest.mark.asyncio
async def test_first_link_filters_endpoints(client_with_contacts, app_with_contacts, monkeypatch):
    dir_resp_body = {
        "peer": _PEER_FP,
        "username": _PEER_USERNAME,
        "display_name": "Remote Peer",
        "signing_pubkey": _PEER_SIGNING_PUB,
        "encryption_pubkey": _PEER_ENCRYPTION_PUB,
        "endpoints": ["file:///etc/passwd", "https://peer.example.com:6969", "http:///nohost"],
    }

    async def handler(method, url, **kw):
        return _fake_dir_resp(body=dir_resp_body)

    _patch_account_proxy(monkeypatch, handler)

    resp = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-f/accept", json={"peer_fingerprint": _PEER_FP}
    )
    assert resp.status_code == 200

    store = app_with_contacts.state.contacts_store
    link = await store.get_peer_link(f"hub:{_PEER_FP}")
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://peer.example.com:6969"], f"filter failed: {urls}"


@pytest.mark.asyncio
async def test_safe_endpoints_drops_unparseable_port():
    from tinyagentos.routes.hub import _safe_endpoints

    raw = [
        {"url": "https://peer.example:notaport"},
        {"url": "https://peer.example:99999"},
        {"url": "https://peer.example:8443"},
    ]
    kept = _safe_endpoints(raw)
    urls = [e.get("url") for e in kept]
    assert urls == ["https://peer.example:8443"], f"unparseable ports not dropped: {urls}"


@pytest.mark.asyncio
async def test_empty_link_recovers_on_later_accept(client_with_contacts, app_with_contacts, monkeypatch):
    """First accept advertises only invalid endpoints (stored link endpoints == []),
    second accept advertises a valid one; assert the stored endpoints are now the valid one."""
    # First accept: only invalid endpoints
    dir_resp_body = {
        "peer": _PEER_FP,
        "username": _PEER_USERNAME,
        "display_name": "Remote Peer",
        "signing_pubkey": _PEER_SIGNING_PUB,
        "encryption_pubkey": _PEER_ENCRYPTION_PUB,
        "endpoints": ["https://peer.example:notaport", "https://peer.example:99999"],
    }

    async def handler(method, url, **kw):
        return _fake_dir_resp(body=dir_resp_body)

    _patch_account_proxy(monkeypatch, handler)

    resp = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-empty1/accept", json={"peer_fingerprint": _PEER_FP}
    )
    assert resp.status_code == 200

    store = app_with_contacts.state.contacts_store
    first_link = await store.get_peer_link(f"hub:{_PEER_FP}")
    assert first_link is not None
    assert first_link["endpoints"] == [], f"first link should have empty endpoints: {first_link['endpoints']}"

    # Second accept: valid endpoint
    dir_resp_body["endpoints"] = ["https://peer.example.com:8443"]
    resp2 = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-empty2/accept", json={"peer_fingerprint": _PEER_FP}
    )
    assert resp2.status_code == 200

    link = await store.get_peer_link(f"hub:{_PEER_FP}")
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://peer.example.com:8443"], f"recovery failed: {urls}"
