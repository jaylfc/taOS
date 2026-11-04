"""Endpoint tests for tinyagentos/routes/settings.py update-status ping env flag."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_update_status_ping_disabled_by_env_true(client, monkeypatch):
    """TAOS_NO_UPDATE_PING=1 -> ping_disabled_by_env is True."""
    monkeypatch.setenv("TAOS_NO_UPDATE_PING", "1")
    resp = await client.get("/api/settings/update-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ping_disabled_by_env"] is True


@pytest.mark.asyncio
async def test_update_status_ping_disabled_by_env_unset(client, monkeypatch):
    """TAOS_NO_UPDATE_PING unset -> ping_disabled_by_env is False."""
    monkeypatch.delenv("TAOS_NO_UPDATE_PING", raising=False)
    resp = await client.get("/api/settings/update-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ping_disabled_by_env"] is False


@pytest.mark.asyncio
async def test_update_status_ping_disabled_by_env_zero(client, monkeypatch):
    """TAOS_NO_UPDATE_PING=0 -> ping_disabled_by_env is False."""
    monkeypatch.setenv("TAOS_NO_UPDATE_PING", "0")
    resp = await client.get("/api/settings/update-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ping_disabled_by_env"] is False