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


@pytest.mark.asyncio
async def test_establish_peer_link_keep_first_endpoints():
    """When called twice with keep_existing_endpoints=True, the first endpoints are kept."""
    from tinyagentos.contacts_store import ContactsStore

    import tempfile, os
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test.db"

    store = ContactsStore(db_path=db_path)
    await store.init()

    contact_id = "hub:testfp1234567890abcdef"

    # First call: establish with first endpoints
    result1 = await store.establish_peer_link(
        contact_id=contact_id,
        inbound_token="token1",
        outbound_token="token1_out",
        endpoints=["https://first.example.com:6969"],
        keep_existing_endpoints=False,
    )
    assert result1 is True

    # Second call: establish with different endpoints, but keep_existing_endpoints=True
    result2 = await store.establish_peer_link(
        contact_id=contact_id,
        inbound_token="token2",
        outbound_token="token2_out",
        endpoints=["https://second.example.com:6969"],
        keep_existing_endpoints=True,
    )
    assert result2 is True

    link = await store.get_peer_link(contact_id)
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://first.example.com:6969"], f"kept wrong: {urls}"

    await store.close()
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_establish_peer_link_second_wins_default():
    """With default (keep_existing_endpoints=False), the second write wins."""
    from tinyagentos.contacts_store import ContactsStore

    import tempfile, os
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test.db"

    store = ContactsStore(db_path=db_path)
    await store.init()

    contact_id = "hub:testfp1234567890abcdef"

    # First call: establish with first endpoints
    result1 = await store.establish_peer_link(
        contact_id=contact_id,
        inbound_token="token1",
        outbound_token="token1_out",
        endpoints=["https://first.example.com:6969"],
        keep_existing_endpoints=False,
    )
    assert result1 is True

    # Second call: establish with different endpoints, default (keep_existing_endpoints=False)
    result2 = await store.establish_peer_link(
        contact_id=contact_id,
        inbound_token="token2",
        outbound_token="token2_out",
        endpoints=["https://second.example.com:6969"],
        keep_existing_endpoints=False,
    )
    assert result2 is True

    link = await store.get_peer_link(contact_id)
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://second.example.com:6969"], f"second should win: {urls}"

    await store.close()
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_establish_peer_link_keep_existing_takes_new_when_stored_empty():
    """Stored endpoints [] with keep_existing_endpoints=True takes the new endpoints."""
    from tinyagentos.contacts_store import ContactsStore

    import tempfile, os
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp())
    db_path = tmp_dir / "test.db"

    store = ContactsStore(db_path=db_path)
    await store.init()

    contact_id = "hub:testfp1234567890abcdef"

    # First call: store empty endpoints
    result1 = await store.establish_peer_link(
        contact_id=contact_id,
        inbound_token="token1",
        outbound_token="token1_out",
        endpoints=[],
        keep_existing_endpoints=False,
    )
    assert result1 is True

    # Second call: keep_existing_endpoints=True but stored is empty -> take new
    result2 = await store.establish_peer_link(
        contact_id=contact_id,
        inbound_token="token2",
        outbound_token="token2_out",
        endpoints=["https://second.example.com:6969"],
        keep_existing_endpoints=True,
    )
    assert result2 is True

    link = await store.get_peer_link(contact_id)
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://second.example.com:6969"], f"recovery failed: {urls}"

    await store.close()
    os.unlink(db_path)


@pytest.mark.asyncio
async def test_overlapping_first_accept_keeps_first_endpoints(
    client_with_contacts, app_with_contacts, monkeypatch
):
    """Two overlapping first accepts: both see get_peer_link() == None, so on the
    unfixed code both write with keep_existing_endpoints=False and the second
    overwrites the first link's endpoints.  The fix keeps the FIRST list."""
    first_body = {
        "peer": _PEER_FP,
        "username": _PEER_USERNAME,
        "display_name": "Remote Peer",
        "signing_pubkey": _PEER_SIGNING_PUB,
        "encryption_pubkey": _PEER_ENCRYPTION_PUB,
        "endpoints": ["https://first.example.com:6969"],
    }
    second_body = dict(first_body)
    second_body["endpoints"] = ["https://second.example.com:6969"]

    calls = {"n": 0}

    async def handler(method, url, **kw):
        calls["n"] += 1
        body = first_body if calls["n"] == 1 else second_body
        return _fake_dir_resp(body=body)

    _patch_account_proxy(monkeypatch, handler)

    store = app_with_contacts.state.contacts_store

    # Both accepts observe no existing peer link (the race case).
    async def _get_peer_link_none(contact_id):
        return None

    monkeypatch.setattr(store, "get_peer_link", _get_peer_link_none)

    resp = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-race-1/accept",
        json={"peer_fingerprint": _PEER_FP},
    )
    assert resp.status_code == 200, resp.text

    resp2 = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-race-2/accept",
        json={"peer_fingerprint": _PEER_FP},
    )
    assert resp2.status_code == 200, resp2.text

    # Restore the real reader now that both accepts (which must see None) are done.
    monkeypatch.undo()
    store = app_with_contacts.state.contacts_store
    link = await store.get_peer_link(f"hub:{_PEER_FP}")
    assert link is not None
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://first.example.com:6969"], f"second overwrote first: {urls}"


@pytest.mark.asyncio
async def test_safe_endpoints_drops_loopback_and_link_local():
    """_safe_endpoints drops loopback, link-local, and RFC1918 private IPs."""
    from tinyagentos.routes.hub import _safe_endpoints

    raw = [
        {"url": "http://127.0.0.1:8080"},           # loopback
        {"url": "http://localhost:8080"},          # localhost hostname
        {"url": "http://169.254.169.254:80"},      # link-local (AWS metadata)
        {"url": "http://10.0.0.1:8080"},           # RFC1918 class A
        {"url": "http://172.16.0.1:8080"},         # RFC1918 class B
        {"url": "http://192.168.1.1:8080"},        # RFC1918 class C
        {"url": "http://[::1]:8080"},              # IPv6 loopback
        {"url": "http://[fe80::1]:8080"},          # IPv6 link-local
        {"url": "http://[fc00::1]:8080"},          # IPv6 unique-local
        {"url": "https://peer.example.com:6969"},  # valid public
    ]
    kept = _safe_endpoints(raw)
    urls = [e.get("url") for e in kept]
    assert urls == ["https://peer.example.com:6969"], f"private endpoints not dropped: {urls}"


@pytest.mark.asyncio
async def test_safe_endpoints_drops_blocked_suffixes():
    """_safe_endpoints drops hostnames with blocked suffixes (.local, .onion, etc.)."""
    from tinyagentos.routes.hub import _safe_endpoints

    raw = [
        {"url": "http://myserver.local:8080"},     # .local suffix
        {"url": "http://service.internal:8080"},   # .internal suffix
        {"url": "http://host.corp:8080"},          # .corp suffix
        {"url": "http://machine.lan:8080"},        # .lan suffix
        {"url": "http://server.intranet:8080"},    # .intranet suffix
        {"url": "http://hidden.onion:8080"},       # .onion suffix
        {"url": "https://peer.example.com:6969"},  # valid public
    ]
    kept = _safe_endpoints(raw)
    urls = [e.get("url") for e in kept]
    assert urls == ["https://peer.example.com:6969"], f"blocked suffixes not dropped: {urls}"


@pytest.mark.asyncio
async def test_first_link_drops_private_endpoints(client_with_contacts, app_with_contacts, monkeypatch):
    """Friend-accept drops private/loopback/link-local endpoints from directory response."""
    dir_resp_body = {
        "peer": _PEER_FP,
        "username": _PEER_USERNAME,
        "display_name": "Remote Peer",
        "signing_pubkey": _PEER_SIGNING_PUB,
        "encryption_pubkey": _PEER_ENCRYPTION_PUB,
        "endpoints": [
            "http://127.0.0.1:8080",
            "http://192.168.1.100:8080",
            "https://peer.example.com:6969",
        ],
    }

    async def handler(method, url, **kw):
        return _fake_dir_resp(body=dir_resp_body)

    _patch_account_proxy(monkeypatch, handler)

    resp = await client_with_contacts.post(
        "/api/hub/friends/requests/rid-private/accept", json={"peer_fingerprint": _PEER_FP}
    )
    assert resp.status_code == 200

    store = app_with_contacts.state.contacts_store
    link = await store.get_peer_link(f"hub:{_PEER_FP}")
    urls = [e.get("url") if isinstance(e, dict) else e for e in link["endpoints"]]
    assert urls == ["https://peer.example.com:6969"], f"private endpoints not dropped: {urls}"
