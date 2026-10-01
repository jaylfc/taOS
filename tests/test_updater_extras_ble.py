from __future__ import annotations
import pytest

from tinyagentos.routes import settings as settings_mod


@pytest.mark.asyncio
async def test_installer_extras_selection_handset(monkeypatch):
    """Test that the updater installs .[ble] on a taOSmobile handset."""
    # Patch the _detect_device_class function in the settings module directly
    monkeypatch.setattr('tinyagentos.routes.settings._detect_device_class', lambda: "mobile")

    # Test the uv sync command path
    monkeypatch.setattr('tinyagentos.routes.settings._find_uv', lambda pd: "/opt/uv")

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        return 0, "synced"

    monkeypatch.setattr('tinyagentos.routes.settings._run_capture', fake_run)

    rc, out = await settings_mod._install_dependencies("/srv/taos")

    # Only --extra ble (the LiteLLM proxy extra is gone)
    assert captured["cmd"] == ["/opt/uv", "sync", "--frozen", "--extra", "ble"]


@pytest.mark.asyncio
async def test_installer_extras_selection_non_handset(monkeypatch):
    """Test that the updater installs no extra on a non-handset host."""
    # Patch the _detect_device_class function in the settings module directly
    monkeypatch.setattr('tinyagentos.routes.settings._detect_device_class', lambda: None)

    # Test the uv sync command path
    monkeypatch.setattr('tinyagentos.routes.settings._find_uv', lambda pd: "/opt/uv")

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        return 0, "synced"

    monkeypatch.setattr('tinyagentos.routes.settings._run_capture', fake_run)

    rc, out = await settings_mod._install_dependencies("/srv/taos")

    # No extra at all (the LiteLLM proxy extra is gone)
    assert captured["cmd"] == ["/opt/uv", "sync", "--frozen"]


@pytest.mark.asyncio
async def test_installer_extras_selection_taos_extras_ble_override(monkeypatch):
    """Test that TAOS_EXTRAS_BLE=1 forces ble inclusion even on non-handset."""
    # Patch the _detect_device_class function in the settings module directly
    monkeypatch.setattr('tinyagentos.routes.settings._detect_device_class', lambda: None)

    # Set TAOS_EXTRAS_BLE=1 in environment
    monkeypatch.setenv("TAOS_EXTRAS_BLE", "1")

    # Test the uv sync command path
    monkeypatch.setattr('tinyagentos.routes.settings._find_uv', lambda pd: "/opt/uv")

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        return 0, "synced"

    monkeypatch.setattr('tinyagentos.routes.settings._run_capture', fake_run)

    rc, out = await settings_mod._install_dependencies("/srv/taos")

    # Only --extra ble (the LiteLLM proxy extra is gone)
    assert captured["cmd"] == ["/opt/uv", "sync", "--frozen", "--extra", "ble"]


@pytest.mark.asyncio
async def test_installer_extras_selection_taos_extras_ble_exclude(monkeypatch):
    """Test that TAOS_EXTRAS_BLE=0 forces ble exclusion even on handset."""
    # Patch the _detect_device_class function in the settings module directly
    monkeypatch.setattr('tinyagentos.routes.settings._detect_device_class', lambda: "mobile")

    # Set TAOS_EXTRAS_BLE=0 in environment
    monkeypatch.setenv("TAOS_EXTRAS_BLE", "0")

    # Test the uv sync command path
    monkeypatch.setattr('tinyagentos.routes.settings._find_uv', lambda pd: "/opt/uv")

    captured = {}

    async def fake_run(cmd, cwd=None, timeout=600.0, env=None):
        captured["cmd"] = cmd
        return 0, "synced"

    monkeypatch.setattr('tinyagentos.routes.settings._run_capture', fake_run)

    rc, out = await settings_mod._install_dependencies("/srv/taos")

    # No extra at all (the LiteLLM proxy extra is gone)
    assert captured["cmd"] == ["/opt/uv", "sync", "--frozen"]
