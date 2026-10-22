"""HTTP-layer tests for the taOS Orb status route (routes/orb.py): GET /api/orb/status.

Hostile cases first: non-admin 403, unauthed 401/403, transport failure.
Happy path: admin returns list of orb dicts.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.orb.frames import TransportUnavailable
from tinyagentos.routes.auth import _require_admin


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
        lambda: []
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