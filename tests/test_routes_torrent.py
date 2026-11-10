"""Endpoint tests for tinyagentos/routes/torrent.py."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_torrent_settings_returns_200(client, monkeypatch):
    store = client._transport.app.state.download_manager._torrent_settings_store
    monkeypatch.setattr(
        client._transport.app.state, "torrent_settings_store", store, raising=False
    )
    resp = await client.get("/api/torrent/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_torrent_settings_response_shape(client, monkeypatch):
    store = client._transport.app.state.download_manager._torrent_settings_store
    monkeypatch.setattr(
        client._transport.app.state, "torrent_settings_store", store, raising=False
    )
    data = (await client.get("/api/torrent/settings")).json()
    assert "libtorrent_available" in data
    assert "seed_enabled" in data
    assert "upload_rate_limit_kbps" in data
    assert "max_active_seeds" in data
    assert isinstance(data["libtorrent_available"], bool)
    assert isinstance(data["seed_enabled"], bool)
    assert isinstance(data["upload_rate_limit_kbps"], int)
    assert isinstance(data["max_active_seeds"], int)


@pytest.mark.asyncio
async def test_get_torrent_settings_503_when_store_missing(client, monkeypatch):
    monkeypatch.setattr(
        client._transport.app.state, "torrent_settings_store", None, raising=False
    )
    resp = await client.get("/api/torrent/settings")
    assert resp.status_code == 503
    assert resp.json()["error"] == "torrent settings store not initialised"


@pytest.mark.asyncio
async def test_put_torrent_settings_happy_path(client, monkeypatch):
    store = client._transport.app.state.download_manager._torrent_settings_store
    monkeypatch.setattr(
        client._transport.app.state, "torrent_settings_store", store, raising=False
    )
    body = {
        "seed_enabled": False,
        "upload_rate_limit_kbps": 2048,
        "max_active_seeds": 5,
    }
    resp = await client.put("/api/torrent/settings", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "saved"
    assert data["seed_enabled"] is False
    assert data["upload_rate_limit_kbps"] == 2048
    assert data["max_active_seeds"] == 5


@pytest.mark.asyncio
async def test_put_torrent_settings_503_when_store_missing(client, monkeypatch):
    monkeypatch.setattr(
        client._transport.app.state, "torrent_settings_store", None, raising=False
    )
    resp = await client.put("/api/torrent/settings", json={"seed_enabled": True})
    assert resp.status_code == 503
    assert resp.json()["error"] == "torrent settings store not initialised"


@pytest.mark.asyncio
async def test_put_torrent_settings_requires_admin(client):
    """PUT /api/torrent/settings must require admin dependency."""
    from tinyagentos.auth_context import require_admin as _require_admin
    target_path = "/api/torrent/settings"
    found = False
    for route in client._transport.app.routes:
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", set()) or set()
        if target_path in path and "PUT" in methods:
            deps = route.dependant.dependencies
            assert any(
                d.call is _require_admin or d.call == _require_admin
                for d in deps
            ), f"require_admin not found in dependencies: {[d.call for d in deps]}"
            found = True
            break
    assert found, f"Route {target_path!r} with PUT not found in app routes"


@pytest.mark.asyncio
async def test_put_torrent_settings_non_admin_403(tmp_path):
    """Non-admin member gets 403 on PUT /api/torrent/settings; settings unchanged."""
    from fastapi.testclient import TestClient
    from tinyagentos.app import create_app
    import yaml

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    app = create_app(data_dir=tmp_path)

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    # Invite a member user (like test_local_desktop_non_admin_403 does)
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite(
        "member", invite_code, "Test Member", "", "member-settings-pass-123",
    )
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})
    from taos_test_csrf import arm_test_client

    arm_test_client(client)

    # The test client bypasses the lifespan, so wire the store onto app.state
    # like the other tests in this file do.
    app.state.torrent_settings_store = app.state.download_manager._torrent_settings_store
    before = app.state.torrent_settings_store.load()

    resp = client.put("/api/torrent/settings", json={"seed_enabled": False})
    assert resp.status_code == 403

    # Settings must be unchanged.
    after = app.state.torrent_settings_store.load()
    assert after.seed_enabled == before.seed_enabled
    assert after.upload_rate_limit_kbps == before.upload_rate_limit_kbps
    assert after.max_active_seeds == before.max_active_seeds


@pytest.mark.asyncio
async def test_put_torrent_settings_admin_still_saves(client, monkeypatch):
    """Admin can still PUT torrent settings; GET reflects the change."""
    store = client._transport.app.state.download_manager._torrent_settings_store
    monkeypatch.setattr(
        client._transport.app.state, "torrent_settings_store", store, raising=False
    )
    body = {
        "seed_enabled": False,
        "upload_rate_limit_kbps": 100,
        "max_active_seeds": 3,
    }
    resp = await client.put("/api/torrent/settings", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "saved"
    assert data["seed_enabled"] is False
    assert data["upload_rate_limit_kbps"] == 100
    assert data["max_active_seeds"] == 3
    # GET reflects the saved value
    get_resp = await client.get("/api/torrent/settings")
    assert get_resp.status_code == 200
    assert get_resp.json()["seed_enabled"] is False


@pytest.mark.asyncio
async def test_put_torrent_settings_422_on_invalid_body(client):
    resp = await client.put(
        "/api/torrent/settings",
        json={"upload_rate_limit_kbps": -1},
    )
    assert resp.status_code == 422
