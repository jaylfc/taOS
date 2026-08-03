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
async def test_put_torrent_settings_422_on_invalid_body(client):
    resp = await client.put(
        "/api/torrent/settings",
        json={"upload_rate_limit_kbps": -1},
    )
    assert resp.status_code == 422
