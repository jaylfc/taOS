"""Tests for hub friend-accept -> contact row + peer-link handshake (collab A2).

Covers: contact creation on accept, peer-link establishment, block cascade
to contacts_store.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks

from tinyagentos.contacts_store import generate_peer_token, _hash_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_dir_resp(status=200, body=None):
    """Build a fake upstream HTTP response matching _forward_to's interface."""
    if body is None:
        body = {}
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def _patch_account_proxy(monkeypatch, handler):
    """Intercept httpx.AsyncClient.request for calls to _UPSTREAM."""
    _UPSTREAM = "https://taos.my"
    orig = httpx.AsyncClient.request

    async def routed(self, method, url, **kw):
        url_s = str(url)
        if url_s.startswith(_UPSTREAM):
            return await handler(method, url_s, **kw)
        return await orig(self, method, url, **kw)

    monkeypatch.setattr("httpx.AsyncClient.request", routed)


def _bootstrap_hub_identity(data_dir: Path, username: str = "localnode") -> str:
    """Create a hub identity keystore + author row, return local id."""
    import sqlite3

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
            fingerprint TEXT PRIMARY KEY,
            username TEXT,
            signing_pubkey TEXT,
            encryption_pubkey TEXT,
            updated_at REAL
        )"""
    )
    conn.execute(
        "INSERT OR REPLACE INTO hub_authors (fingerprint, username, signing_pubkey, encryption_pubkey, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (fp, username, ident["signing_public"], ident["encryption_public"], time.time()),
    )
    # Also create hub_relationships and hub_objects for completeness
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS hub_objects (
            hash TEXT PRIMARY KEY, author TEXT NOT NULL, type TEXT NOT NULL,
            seq INTEGER, version INTEGER, body TEXT NOT NULL, created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hub_relationships (
            peer TEXT NOT NULL, kind TEXT NOT NULL, statement TEXT,
            quota_hint INTEGER, updated_at REAL NOT NULL,
            PRIMARY KEY (peer, kind)
        );
        CREATE TABLE IF NOT EXISTS hub_chain (
            author TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL,
            prev_hash TEXT, type TEXT NOT NULL, target TEXT, created_at REAL NOT NULL,
            PRIMARY KEY (author, seq)
        );
        """
    )
    conn.commit()
    conn.close()
    return f"hub:{username}"


_PEER_FP = "9a2db2e23f1504cd056606553ac049c5e718e8f9ce9233876df1a7a1821af885"  # SHA-256 of _PEER_SIGNING_PUB
_PEER_USERNAME = "remotepeer"
_PEER_SIGNING_PUB = "ab" * 32  # 64-char fake Ed25519 pubkey
_PEER_ENCRYPTION_PUB = "cd" * 32  # 64-char fake X25519 pubkey

# A second, distinct peer that shares _PEER_USERNAME's username but has a
# different signing key (hence a different fingerprint).  Used to prove the
# contact key is fingerprint-based: a username collision must not overwrite
# the first contact's pinned key material.
_PEER2_FP = "b9c61610704cb9b9ea441aa8afe5d7d8e852a30f918001cda5c19951ffb62aad"  # SHA-256 of _PEER2_SIGNING_PUB
_PEER2_SIGNING_PUB = "ef" * 32
_PEER2_ENCRYPTION_PUB = "fe" * 32


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_with_contacts(tmp_data_dir, monkeypatch):
    """Create an app with contacts_store and a bootstrapped hub identity."""
    from tinyagentos.app import create_app

    _app = create_app(data_dir=tmp_data_dir)

    # Initialise contacts_store
    store = _app.state.contacts_store
    if store._db is not None:
        await store.close()
    await store.init()

    # Bootstrap hub identity
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
    _bootstrap_hub_identity(tmp_data_dir)

    yield _app

    # Cleanup: close the contacts_store so the on-disk database file
    # is released before tmp_data_dir (tmp_path) tears down.
    store = _app.state.contacts_store
    if store is not None and store._db is not None:
        await store.close()


@pytest_asyncio.fixture
async def client_with_contacts(app_with_contacts):
    """Async client with contacts_store, auth, and proxied directory."""
    _app = app_with_contacts

    _app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = _app.state.auth.find_user("admin")
    _uid = _rec["id"] if _rec else ""
    _token = _app.state.auth.create_session(user_id=_uid, long_lived=True)
    _app.state._startup_complete = True

    transport = ASGITransport(app=_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: friend-accept -> contact + peer link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFriendAcceptHandshake:
    async def test_accept_creates_contact_and_peer_link(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Accepting a friend request creates a contact row and peer link."""
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "display_name": "Remote Peer",
            "signing_pubkey": _PEER_SIGNING_PUB,
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
            "endpoints": ["https://peer.example.com:6969"],
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "accepted"
        assert data["peer"] == _PEER_FP

        # Verify contact row was created
        store = app_with_contacts.state.contacts_store
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact is not None, "contact should be created on accept"
        assert contact["hub_username"] == _PEER_USERNAME
        assert contact["display_name"] == "Remote Peer"
        assert contact["ed25519_pub"] == _PEER_SIGNING_PUB
        assert contact["x25519_pub"] == _PEER_ENCRYPTION_PUB
        assert contact["status"] == "active"

        # Verify peer link was established
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link is not None, "peer link should be established on accept"
        assert link["endpoints"] == [{"kind": "hub", "url": "https://peer.example.com:6969", "priority": 0}]
        # inbound_token should be a fresh token
        assert link["inbound_token_hash"] is not None
        # outbound_token is empty placeholder until A3 handshake reply
        assert link["outbound_token"] == ""

    async def test_accept_falls_back_to_hub_authors(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """When the directory omits pubkeys, fall back to hub_authors."""
        # Directory response *without* pubkeys
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "display_name": "Remote Peer",
            "endpoints": ["https://peer.example.com:6969"],
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        # Pre-populate hub_authors so the fallback works
        from tinyagentos.hub.store import HubStore
        hub_store = HubStore(
            Path(app_with_contacts.state.data_dir) / "hub" / "hub.db"
        )
        try:
            await hub_store.init()
            await hub_store.upsert_author(
                _PEER_FP,
                username=_PEER_USERNAME,
                signing_pubkey=_PEER_SIGNING_PUB,
                encryption_pubkey=_PEER_ENCRYPTION_PUB,
            )
        finally:
            await hub_store.close()

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-2/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200

        store = app_with_contacts.state.contacts_store
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact is not None
        assert contact["ed25519_pub"] == _PEER_SIGNING_PUB
        assert contact["x25519_pub"] == _PEER_ENCRYPTION_PUB

    async def test_accept_skips_handshake_when_no_pubkeys(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """When neither directory nor hub_authors have pubkeys, accept still
        succeeds but skips the handshake (no contact row)."""
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-3/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "accepted"

        store = app_with_contacts.state.contacts_store
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact is None, "no contact should be created without pubkeys"
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link is None, "no peer link should be established without pubkeys"

    async def test_accept_handles_non_list_endpoints(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Gracefully handles endpoints that are a string or missing."""
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "signing_pubkey": _PEER_SIGNING_PUB,
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
            "endpoints": '["https://peer.example.com:6969"]',  # JSON string
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-ep/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200

        store = app_with_contacts.state.contacts_store
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link is not None
        assert link["endpoints"] == [{"kind": "hub", "url": "https://peer.example.com:6969", "priority": 0}]

    async def test_accept_reupsert_contact(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Re-accepting a friend (re-establish) refreshes the contact and link,
        and clears a prior revocation."""
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "signing_pubkey": _PEER_SIGNING_PUB,
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
            "endpoints": ["https://first.example.com:6969"],
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        # First accept
        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-re/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200

        store = app_with_contacts.state.contacts_store
        first_link = await store.get_peer_link(f"hub:{_PEER_FP}")
        first_established = first_link["established_at"]

        # Simulate a revocation so we can verify re-establish actually clears it.
        await store.revoke_peer_link(f"hub:{_PEER_FP}")
        revoked_link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert revoked_link["revoked_at"] is not None, "revocation must stick"

        # Second accept with different endpoints — should re-establish and clear
        dir_resp_body["endpoints"] = ["https://second.example.com:6969"]
        resp2 = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-re/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp2.status_code == 200

        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link is not None
        assert link["endpoints"] == [{"kind": "hub", "url": "https://second.example.com:6969", "priority": 0}]
        assert link["revoked_at"] is None, "re-establish must clear revocation"

    async def test_accept_without_contacts_store_does_not_crash(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Handshake is best-effort — missing contacts_store must not break accept."""
        app_with_contacts.state.contacts_store = None

        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "signing_pubkey": _PEER_SIGNING_PUB,
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-nocs/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "accepted"

    async def test_accept_same_username_second_peer_does_not_overwrite(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """A second peer sharing an existing contact's username must pin its own
        fingerprint-keyed contact without overwriting the first's key material."""
        store = app_with_contacts.state.contacts_store

        # First peer: username "remotepeer", fingerprint _PEER_FP.
        dir_resp_1 = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "display_name": "Remote One",
            "signing_pubkey": _PEER_SIGNING_PUB,
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
            "endpoints": ["https://one.example.com:6969"],
        }

        async def handler1(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_1)

        _patch_account_proxy(monkeypatch, handler1)
        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-dup1/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200

        first = await store.get_contact(f"hub:{_PEER_FP}")
        assert first is not None
        assert first["ed25519_pub"] == _PEER_SIGNING_PUB
        assert first["x25519_pub"] == _PEER_ENCRYPTION_PUB

        # Second peer: SAME username, different fingerprint + key material.
        dir_resp_2 = {
            "peer": _PEER2_FP,
            "username": _PEER_USERNAME,
            "display_name": "Remote Two",
            "signing_pubkey": _PEER2_SIGNING_PUB,
            "encryption_pubkey": _PEER2_ENCRYPTION_PUB,
            "endpoints": ["https://two.example.com:6969"],
        }

        async def handler2(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_2)

        _patch_account_proxy(monkeypatch, handler2)
        resp2 = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-dup2/accept",
            json={"peer_fingerprint": _PEER2_FP},
        )
        assert resp2.status_code == 200

        # The first contact's pins are intact — NOT overwritten by the name twin.
        first_again = await store.get_contact(f"hub:{_PEER_FP}")
        assert first_again is not None
        assert first_again["ed25519_pub"] == _PEER_SIGNING_PUB
        assert first_again["x25519_pub"] == _PEER_ENCRYPTION_PUB
        assert first_again["peer_fingerprint"] == _PEER_FP

        # The second peer got its own distinct, fingerprint-keyed contact.
        second = await store.get_contact(f"hub:{_PEER2_FP}")
        assert second is not None
        assert second["ed25519_pub"] == _PEER2_SIGNING_PUB
        assert second["x25519_pub"] == _PEER2_ENCRYPTION_PUB
        assert second["peer_fingerprint"] == _PEER2_FP

        # Both share a username but are distinct contacts.
        assert second["hub_username"] == first_again["hub_username"] == _PEER_USERNAME


# ---------------------------------------------------------------------------
# Tests: block -> cascade to contacts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBlockCascade:
    async def test_block_cascades_to_contacts_store(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Blocking a friend revokes the peer link."""
        # First, create a contact and peer link so there's something to revoke.
        store = app_with_contacts.state.contacts_store
        await store.add_contact(
            contact_id=f"hub:{_PEER_FP}",
            hub_username=_PEER_USERNAME,
            display_name="Remote",
            ed25519_pub=_PEER_SIGNING_PUB,
            x25519_pub=_PEER_ENCRYPTION_PUB,
            peer_fingerprint=_PEER_FP,
        )
        await store.establish_peer_link(
            contact_id=f"hub:{_PEER_FP}",
            inbound_token=generate_peer_token(),
            outbound_token=generate_peer_token(),
        )

        # Pre-populate hub_authors so block can resolve fingerprint -> username.
        from tinyagentos.hub.store import HubStore
        hub_store = HubStore(
            Path(app_with_contacts.state.data_dir) / "hub" / "hub.db"
        )
        try:
            await hub_store.init()
            await hub_store.upsert_author(
                _PEER_FP,
                username=_PEER_USERNAME,
                signing_pubkey=_PEER_SIGNING_PUB,
                encryption_pubkey=_PEER_ENCRYPTION_PUB,
            )
        finally:
            await hub_store.close()

        # Mock the directory block edge revoke call (best-effort, must not fail block).
        async def handler(method, url, **kw):
            if "/api/hub/edges/revoke" in url:
                return _fake_dir_resp(body={"status": "revoked"})
            return _fake_dir_resp(body={})

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/block",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "blocked"

        # Verify peer link is revoked
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link["revoked_at"] is not None

        # Verify contact is blocked
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact["status"] == "blocked"

    async def test_block_cascade_handles_missing_contacts_store(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Block must succeed even when contacts_store is unavailable."""
        app_with_contacts.state.contacts_store = None

        async def handler(method, url, **kw):
            if "/api/hub/edges/revoke" in url:
                return _fake_dir_resp(body={"status": "revoked"})
            return _fake_dir_resp(body={})

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/block",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "blocked"

    async def test_block_cascade_fingerprint_fallback(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Block revokes peer link via fingerprint fallback when hub_authors is empty."""
        store = app_with_contacts.state.contacts_store

        # Create contact + peer link with fingerprint, but do NOT seed hub_authors.
        await store.add_contact(
            contact_id=f"hub:{_PEER_FP}",
            hub_username=_PEER_USERNAME,
            display_name="Remote",
            ed25519_pub=_PEER_SIGNING_PUB,
            x25519_pub=_PEER_ENCRYPTION_PUB,
            peer_fingerprint=_PEER_FP,
        )
        await store.establish_peer_link(
            contact_id=f"hub:{_PEER_FP}",
            inbound_token=generate_peer_token(),
            outbound_token=generate_peer_token(),
        )

        # Mock directory block-edge revoke.
        async def handler(method, url, **kw):
            if "/api/hub/edges/revoke" in url:
                return _fake_dir_resp(body={"status": "revoked"})
            return _fake_dir_resp(body={})

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/block",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "blocked"

        # Verify peer link is revoked (fingerprint fallback worked)
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link["revoked_at"] is not None

        # Verify contact is blocked
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact["status"] == "blocked"

    async def test_block_cascade_revokes_when_cached_username_stale(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Block revokes the peer link even when the hub_authors username cache
        is present-but-stale (renamed since the contact was pinned)."""
        store = app_with_contacts.state.contacts_store

        # Contact pinned under the fingerprint, with the ORIGINAL username.
        await store.add_contact(
            contact_id=f"hub:{_PEER_FP}",
            hub_username="original-name",
            display_name="Remote",
            ed25519_pub=_PEER_SIGNING_PUB,
            x25519_pub=_PEER_ENCRYPTION_PUB,
            peer_fingerprint=_PEER_FP,
        )
        await store.establish_peer_link(
            contact_id=f"hub:{_PEER_FP}",
            inbound_token=generate_peer_token(),
            outbound_token=generate_peer_token(),
        )

        # Seed hub_authors with a DIFFERENT (stale) username for the same
        # fingerprint — the peer renamed after the contact was pinned.
        from tinyagentos.hub.store import HubStore

        hub_store = HubStore(
            Path(app_with_contacts.state.data_dir) / "hub" / "hub.db"
        )
        try:
            await hub_store.init()
            await hub_store.upsert_author(
                _PEER_FP,
                username="renamed-later",
                signing_pubkey=_PEER_SIGNING_PUB,
                encryption_pubkey=_PEER_ENCRYPTION_PUB,
            )
        finally:
            await hub_store.close()

        async def handler(method, url, **kw):
            if "/api/hub/edges/revoke" in url:
                return _fake_dir_resp(body={"status": "revoked"})
            return _fake_dir_resp(body={})

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/block",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "blocked"

        # Revocation resolved via the fingerprint, not the stale username.
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link["revoked_at"] is not None
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact["status"] == "blocked"

    async def test_block_cascade_revokes_all_contacts_sharing_fingerprint(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Block revokes every contact pinned to the fingerprint, not just the
        first row — legacy username-keyed contacts can share a fingerprint, and
        revoking only rows[0] would leave a live peer link behind."""
        store = app_with_contacts.state.contacts_store

        # Two legacy contacts keyed on DIFFERENT usernames but the SAME
        # fingerprint (the state a rename-before-accept used to produce).
        legacy_ids = ("hub:legacy-name-one", "hub:legacy-name-two")
        for legacy_id, name in (
            (legacy_ids[0], "legacy-name-one"),
            (legacy_ids[1], "legacy-name-two"),
        ):
            await store.add_contact(
                contact_id=legacy_id,
                hub_username=name,
                display_name="Remote",
                ed25519_pub=_PEER_SIGNING_PUB,
                x25519_pub=_PEER_ENCRYPTION_PUB,
                peer_fingerprint=_PEER_FP,
            )
            await store.establish_peer_link(
                contact_id=legacy_id,
                inbound_token=generate_peer_token(),
                outbound_token=generate_peer_token(),
            )

        async def handler(method, url, **kw):
            if "/api/hub/edges/revoke" in url:
                return _fake_dir_resp(body={"status": "revoked"})
            return _fake_dir_resp(body={})

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/block",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "blocked"

        # BOTH legacy contacts must be revoked and blocked, not just the first.
        for legacy_id in legacy_ids:
            link = await store.get_peer_link(legacy_id)
            assert link["revoked_at"] is not None, (
                f"peer link {legacy_id} must be revoked"
            )
            contact = await store.get_contact(legacy_id)
            assert contact["status"] == "blocked", (
                f"contact {legacy_id} must be blocked"
            )


# ---------------------------------------------------------------------------
# Security regression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSecurityRegression:
    async def test_anti_imposter_mismatched_pubkey(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """A directory response with a signing_pubkey that does NOT match the
        peer fingerprint must NOT create a contact — prevents an imposter from
        hijacking the handshake."""
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "display_name": "Imposter",
            "signing_pubkey": "ff" * 32,  # WRONG — does not hash to _PEER_FP
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
            "endpoints": ["https://imposter.example.com:6969"],
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-imp/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        # Should still return 200 (accept doesn't fail) but NO contact created
        assert resp.status_code == 200

        store = app_with_contacts.state.contacts_store
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact is None, "imposter pubkey must not create a contact"
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link is None, "imposter pubkey must not create a peer link"

    async def test_authz_rejection_no_handshake(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """A failed directory lookup (403/404) must NOT establish a contact
        or peer link — the handshake must fail closed. The route returns
        the upstream status code on failure."""
        # Directory returns 403
        async def handler(method, url, **kw):
            return _fake_dir_resp(
                status=403, body={"error": "forbidden"}
            )

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-403/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        # Route passes upstream status; no contact/handshake happens
        assert resp.status_code == 403
        data = resp.json()
        assert data["state"] == "rejected"

        store = app_with_contacts.state.contacts_store
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact is None, "403 must not create a contact"
        link = await store.get_peer_link(f"hub:{_PEER_FP}")
        assert link is None, "403 must not create a peer link"

    async def test_block_guard_prevent_reaccept_resurrection(
        self, client_with_contacts, app_with_contacts, monkeypatch
    ):
        """Blocking a peer then re-accepting the same fingerprint must NOT
        resurrect the contact — the block guard prevents it."""
        store = app_with_contacts.state.contacts_store

        # Create a contact and peer link, then block.
        await store.add_contact(
            contact_id=f"hub:{_PEER_FP}",
            hub_username=_PEER_USERNAME,
            display_name="Remote",
            ed25519_pub=_PEER_SIGNING_PUB,
            x25519_pub=_PEER_ENCRYPTION_PUB,
            peer_fingerprint=_PEER_FP,
        )
        await store.establish_peer_link(
            contact_id=f"hub:{_PEER_FP}",
            inbound_token=generate_peer_token(),
            outbound_token=generate_peer_token(),
        )
        await store.set_contact_status(f"hub:{_PEER_FP}", "blocked")

        # Also add REL_BLOCK on hub relationships — the accept guard
        # checks has_edge(peer, REL_BLOCK) at the hub layer, not the
        # contacts layer.
        from tinyagentos.hub.store import HubStore
        hub_store = HubStore(
            Path(app_with_contacts.state.data_dir) / "hub" / "hub.db"
        )
        try:
            await hub_store.init()
            await hub_store.put_relationship(_PEER_FP, "block")
        finally:
            await hub_store.close()

        # Now try to re-accept
        dir_resp_body = {
            "peer": _PEER_FP,
            "username": _PEER_USERNAME,
            "display_name": "Remote Peer",
            "signing_pubkey": _PEER_SIGNING_PUB,
            "encryption_pubkey": _PEER_ENCRYPTION_PUB,
            "endpoints": ["https://peer.example.com:6969"],
        }

        async def handler(method, url, **kw):
            return _fake_dir_resp(body=dir_resp_body)

        _patch_account_proxy(monkeypatch, handler)

        resp = await client_with_contacts.post(
            "/api/hub/friends/requests/test-rid-res/accept",
            json={"peer_fingerprint": _PEER_FP},
        )
        assert resp.status_code == 200

        # Contact must still be blocked — NOT resurrected to active
        contact = await store.get_contact(f"hub:{_PEER_FP}")
        assert contact is not None
        assert contact["status"] == "blocked", (
            "blocked contact must not be resurrected by re-accept"
        )
