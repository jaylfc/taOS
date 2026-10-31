"""Local desktop state endpoint tests.

Tests for GET /api/system/local-desktop — reads systemctl state for
taos-kiosk.service to report available/enabled/active.
"""
from __future__ import annotations

import pytest

from tinyagentos.routes import system as system_routes


@pytest.mark.asyncio
async def test_local_desktop_unit_absent(monkeypatch, tmp_path):
    """When taos-kiosk.service is absent, all flags are false and is-enabled/is-active are not called."""
    async def fake(*args):
        if args[0] == "cat":
            return 1, "No files found for taos-kiosk.service."
        if args[0] == "is-enabled":
            raise AssertionError("is-enabled should not be called when unit is absent")
        if args[0] == "is-active":
            raise AssertionError("is-active should not be called when unit is absent")
        return 1, "unknown"

    monkeypatch.setattr(system_routes, "_systemctl", fake)

    from fastapi.testclient import TestClient
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(__import__("yaml").dump(config))
    app = create_app(data_dir=tmp_path)

    # Set up admin session
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})

    resp = client.get("/api/system/local-desktop")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": False, "enabled": False, "active": False}


@pytest.mark.asyncio
async def test_local_desktop_installed_disabled_inactive(monkeypatch, tmp_path):
    """When unit is installed but disabled and inactive."""
    async def fake(*args):
        if args[0] == "cat":
            return 0, "# taos-kiosk.service"
        if args[0] == "is-enabled":
            return 1, "disabled"
        if args[0] == "is-active":
            return 3, "inactive"
        return 1, "unknown"

    monkeypatch.setattr(system_routes, "_systemctl", fake)

    from fastapi.testclient import TestClient
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(__import__("yaml").dump(config))
    app = create_app(data_dir=tmp_path)

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})

    resp = client.get("/api/system/local-desktop")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": True, "enabled": False, "active": False}


@pytest.mark.asyncio
async def test_local_desktop_enabled_active(monkeypatch, tmp_path):
    """When unit is installed, enabled, and active."""
    async def fake(*args):
        if args[0] == "cat":
            return 0, "# taos-kiosk.service"
        if args[0] == "is-enabled":
            return 0, "enabled"
        if args[0] == "is-active":
            return 0, "active"
        return 1, "unknown"

    monkeypatch.setattr(system_routes, "_systemctl", fake)

    from fastapi.testclient import TestClient
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(__import__("yaml").dump(config))
    app = create_app(data_dir=tmp_path)

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})

    resp = client.get("/api/system/local-desktop")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": True, "enabled": True, "active": True}


@pytest.mark.asyncio
async def test_local_desktop_is_enabled_static(monkeypatch, tmp_path):
    """When is-enabled prints 'static', enabled should be False."""
    async def fake(*args):
        if args[0] == "cat":
            return 0, "# taos-kiosk.service"
        if args[0] == "is-enabled":
            return 0, "static"
        if args[0] == "is-active":
            return 0, "active"
        return 1, "unknown"

    monkeypatch.setattr(system_routes, "_systemctl", fake)

    from fastapi.testclient import TestClient
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(__import__("yaml").dump(config))
    app = create_app(data_dir=tmp_path)

    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})

    resp = client.get("/api/system/local-desktop")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"available": True, "enabled": False, "active": True}


@pytest.mark.asyncio
async def test_local_desktop_non_admin_403(monkeypatch, tmp_path):
    """Non-admin caller gets 403."""
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
    # Invite a member user (like conftest._member_client does)
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite(
        "member", invite_code, "Test Member", "", "membersettingspass123",
    )
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    client = TestClient(app, cookies={"taos_session": token})

    resp = client.get("/api/system/local-desktop")
    assert resp.status_code == 403