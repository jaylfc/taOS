"""HTTP-layer tests for the taOS Orb status route (routes/orb.py): GET /api/orb/status.

Hostile cases first: non-admin 403, unauthed 401/403, transport failure.
Happy path: admin returns list of orb dicts.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.orb.frames import TransportUnavailable


@pytest_asyncio.fixture
async def unauthed_client(app, tmp_data_dir):
    """Client with no session cookie (mirrors test_routes_cluster_ble.py)."""
    store = app.state.cluster_pairing
    await store.init()
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await store.close()


# ---------------------------------------------------------------------------
# Admin gate: every route 403s a non-admin session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/status")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_status_non_admin_session_gets_403(client, app, monkeypatch):
    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_admin_empty_list(client, app, monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.routes.orb.list_usb_orbs",
        list
    )
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"orbs": []}


@pytest.mark.asyncio
async def test_status_admin_with_two_orbs(client, app, monkeypatch):
    fake_orbs = [
        {
            "port": "/dev/ttyACM0",
            "vid": 0x303A,
            "pid": 0x1234,
            "serial_number": "SN123456",
            "description": "taOS Orb",
        },
        {
            "port": "/dev/ttyACM1",
            "vid": 0x303A,
            "pid": 0x5678,
            "serial_number": "SN789012",
            "description": "taOS Orb",
        },
    ]
    monkeypatch.setattr(
        "tinyagentos.routes.orb.list_usb_orbs",
        lambda: fake_orbs
    )
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"orbs": fake_orbs}


@pytest.mark.asyncio
async def test_status_admin_transport_unavailable(client, app, monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.routes.orb.list_usb_orbs",
        lambda: (_ for _ in ()).throw(TransportUnavailable("test error"))
    )
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 503
    body = resp.json()
    assert body == {"error": "orb_transport_unavailable"}

# ---------------------------------------------------------------------------
# GET /api/orb/lan (LAN discovery: mDNS + Agent Buddy)
# ---------------------------------------------------------------------------

import asyncio
import threading

from tinyagentos.orb.frames import TransportUnavailable


@pytest.mark.asyncio
async def test_lan_unauthed_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/lan")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_lan_non_admin_session_gets_403(client, app, monkeypatch):
    def raise_assert(*args, **kwargs):
        raise AssertionError("lookup must not be called for non-admin")

    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", raise_assert)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lan_admin_both_succeed(client, app, monkeypatch):
    fake_lan = [{"name": "orb-1", "addresses": ["192.168.1.10"], "port": 5353}]
    fake_buddy = [{"device_id": "abcd1234abcd1234", "hostname": "buddy1", "address": "192.168.1.20", "port": 4666, "boot": 1}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: fake_buddy)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_orbs"] == fake_lan
    assert body["agent_buddy"] == fake_buddy
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_lan_admin_probe_agent_buddy_transport_unavailable(client, app, monkeypatch):
    fake_lan = [{"name": "orb-1", "addresses": ["192.168.1.10"], "port": 5353}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr(
        "tinyagentos.routes.orb.probe_agent_buddy",
        lambda: (_ for _ in ()).throw(TransportUnavailable("x")),
    )
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_orbs"] == fake_lan
    assert body["agent_buddy"] == []
    assert body["errors"] == [{"source": "agent_buddy", "error": "unavailable"}]


@pytest.mark.asyncio
async def test_lan_admin_browse_lan_orbs_oserror(client, app, monkeypatch):
    fake_buddy = [{"device_id": "abcd1234abcd1234", "hostname": "buddy1", "address": "192.168.1.20", "port": 4666, "boot": 1}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: fake_buddy)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_orbs"] == []
    assert body["agent_buddy"] == fake_buddy
    assert body["errors"] == [{"source": "mdns", "error": "unavailable"}]


@pytest.mark.asyncio
async def test_lan_admin_browse_lan_orbs_runtime_error_raised(client, app, monkeypatch):
    fake_buddy = []
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: fake_buddy)
    with pytest.raises(RuntimeError):
        await client.get("/api/orb/lan")


@pytest.mark.asyncio
async def test_lan_concurrent_execution_no_sleep(client, app, monkeypatch):
    barrier = threading.Barrier(2, timeout=2)

    def fake_browse():
        barrier.wait()
        return []

    def fake_probe():
        barrier.wait()
        return []

    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", fake_browse)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", fake_probe)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/orb/bridges (admin-gated, shared _gather_lan(), evaluate_all())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridges_unauthed_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/bridges")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_bridges_non_admin_session_gets_403(client, app, monkeypatch):
    def raise_assert(*args, **kwargs):
        raise AssertionError("lookup must not be called for non-admin")

    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.evaluate_all", raise_assert)
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bridges_admin_one_orb_mimiclaw_target(client, app, monkeypatch):
    fake_lan = [
        {
            "name": "orb-1",
            "addresses": ["192.168.1.20"],
            "port": 80,
            "board": "x",
            "app": "mimiclaw",
            "version": "1",
        }
    ]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: [])
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    bridges = body["bridges"]
    assert any(b.get("bridge_id") == "mimiclaw" for b in bridges)
    mimiclaw = next(b for b in bridges if b.get("bridge_id") == "mimiclaw")
    targets = mimiclaw["targets"]
    assert any(t.get("address") == "192.168.1.20" for t in targets)


@pytest.mark.asyncio
async def test_bridges_admin_probe_transport_unavailable(client, app, monkeypatch):
    fake_lan = [{"name": "orb-1", "addresses": ["192.168.1.10"], "port": 5353}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr(
        "tinyagentos.routes.orb.probe_agent_buddy",
        lambda: (_ for _ in ()).throw(TransportUnavailable("x")),
    )
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == [{"source": "agent_buddy", "error": "unavailable"}]
    assert "bridges" in body


@pytest.mark.asyncio
async def test_bridges_admin_evaluate_all_passthrough(client, app, monkeypatch):
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: [])
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: [])
    monkeypatch.setattr(
        "tinyagentos.routes.orb.evaluate_all",
        lambda lan_orbs, agent_buddy: [{"bridge_id": "x"}],
    )
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bridges"] == [{"bridge_id": "x"}]
