"""Offline model (LFM3 1.7B on-device): GET/PUT /api/system/offline-model.

The helper runs start/stop through passwordless sudo and status without it;
the PUT is session-only admin (the host local token and agent tokens are
refused).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import tinyagentos.routes.offline_model as om


def _fake_proc(returncode: int = 0, out: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(out, b""))
    return proc


def _helper_cmd(mock_exec) -> tuple[str, ...]:
    return tuple(str(arg) for arg in mock_exec.call_args.args)


async def _member_client(app) -> AsyncClient:
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite("member", invite_code, "Test Member", "", "memberpass123")
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


async def _make_available(app, tmp_path, monkeypatch) -> tuple[Path, Path]:
    """Point the route at a fake helper + metadata and init the settings store."""
    helper = tmp_path / "taos-llm"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    helper.chmod(0o755)
    metadata = tmp_path / "lfm3-1.7b.json"
    metadata.write_text(
        '{"name": "LFM3 1.7B", "licence": "Apache-2.0", '
        '"licence_note": "distributable", "performance_note": "runs on-device"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(om, "HELPER_PATH", helper)
    monkeypatch.setattr(om, "METADATA_PATH", metadata)
    store = app.state.desktop_settings
    if store._db is None:
        await store.init()
    return helper, metadata


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_unavailable_when_helper_missing(client, app, monkeypatch, tmp_path):
    monkeypatch.setattr(om, "HELPER_PATH", tmp_path / "no-such-helper")
    monkeypatch.setattr(om, "METADATA_PATH", tmp_path / "no-such-meta")
    resp = await client.get("/api/system/offline-model")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert data["enabled"] is False
    assert data["running"] is False
    assert data["licence_note"] == ""
    assert data["performance_note"] == ""


@pytest.mark.asyncio
async def test_get_unavailable_when_metadata_missing(client, app, monkeypatch, tmp_path):
    helper = tmp_path / "taos-llm"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(om, "HELPER_PATH", helper)
    monkeypatch.setattr(om, "METADATA_PATH", tmp_path / "no-such-meta")
    resp = await client.get("/api/system/offline-model")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_maps_status_fields(client, app, tmp_path, monkeypatch):
    await _make_available(app, tmp_path, monkeypatch)
    fake = _fake_proc(
        0,
        b'{"running": true, "healthy": true, "model": "LFM3 1.7B", "port": 7835}',
    )
    with patch(
        "tinyagentos.routes.offline_model.asyncio.create_subprocess_exec",
        return_value=fake,
    ):
        resp = await client.get("/api/system/offline-model")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert data["enabled"] is False
    assert data["running"] is True
    assert data["healthy"] is True
    assert data["model"] == "LFM3 1.7B (on this phone, offline)"
    assert data["licence"] == "Apache-2.0"
    assert data["licence_note"] == "distributable"
    assert data["performance_note"] == "runs on-device"


# ---------------------------------------------------------------------------
# Enable / disable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enable_runs_sudo_start_and_stores_pref(client, app, tmp_path, monkeypatch):
    helper, _ = await _make_available(app, tmp_path, monkeypatch)
    fake = _fake_proc(0, b'{"running": true, "healthy": true, "model": "LFM3 1.7B"}')
    with patch(
        "tinyagentos.routes.offline_model.asyncio.create_subprocess_exec",
        return_value=fake,
    ) as mock_exec:
        resp = await client.put("/api/system/offline-model", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    assert _helper_cmd(mock_exec) == ("sudo", "-n", str(helper), "start")
    data = resp.json()
    assert data["status"] == "enabled"
    assert data["running"] is True
    store = app.state.desktop_settings
    prefs = await store.get_preference("user", "offline_model")
    assert prefs == {"enabled": True}


@pytest.mark.asyncio
async def test_disable_runs_stop(client, app, tmp_path, monkeypatch):
    helper, _ = await _make_available(app, tmp_path, monkeypatch)
    fake = _fake_proc(0, b'{"running": false}')
    with patch(
        "tinyagentos.routes.offline_model.asyncio.create_subprocess_exec",
        return_value=fake,
    ) as mock_exec:
        resp = await client.put("/api/system/offline-model", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    assert _helper_cmd(mock_exec) == ("sudo", "-n", str(helper), "stop")
    data = resp.json()
    assert data["status"] == "disabled"
    store = app.state.desktop_settings
    prefs = await store.get_preference("user", "offline_model")
    assert prefs == {"enabled": False}


# ---------------------------------------------------------------------------
# Start failure -> 502
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_exit_one_is_502_with_helper_message(client, app, tmp_path, monkeypatch):
    await _make_available(app, tmp_path, monkeypatch)
    fake = _fake_proc(1, b"helper: the NPU is busy")
    with patch(
        "tinyagentos.routes.offline_model.asyncio.create_subprocess_exec",
        return_value=fake,
    ):
        resp = await client.put("/api/system/offline-model", json={"enabled": True})
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"] == "helper: the NPU is busy"


@pytest.mark.asyncio
async def test_start_other_non_zero_is_502(client, app, tmp_path, monkeypatch):
    await _make_available(app, tmp_path, monkeypatch)
    fake = _fake_proc(127, b"")
    with patch(
        "tinyagentos.routes.offline_model.asyncio.create_subprocess_exec",
        return_value=fake,
    ):
        resp = await client.put("/api/system/offline-model", json={"enabled": True})
    assert resp.status_code == 502, resp.text


# ---------------------------------------------------------------------------
# Auth: signed-in admin session only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_member_session_refused(client, app):
    mc = await _member_client(app)
    try:
        resp = await mc.put("/api/system/offline-model", json={"enabled": True})
    finally:
        await mc.aclose()
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_local_token_refused(client, app):
    token = app.state.auth.get_local_token()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.put(
            "/api/system/offline-model",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_agent_token_refused(client, app):
    token = app.state.auth.mint_agent_local_token("helper")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.put(
            "/api/system/offline-model",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_no_auth_refused(client, app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.put("/api/system/offline-model", json={"enabled": True})
    assert resp.status_code in (401, 403), resp.text
