"""RK3588 performance-mode settings routes (#361).

The Settings toggle drives the taos-rk3588-perf.service unit that
install-server.sh installs on Rockchip boards: GET reports board support,
unit presence and the unit's enabled/active state; PUT enables/disables
the unit with --now so the governor change takes effect immediately.
systemctl is faked at the routes.settings._systemctl seam and the two
filesystem probes (unit file, device-tree compatible) are pointed at tmp
paths so a test never touches the machine's real /etc or /proc.
"""
from __future__ import annotations

import pytest

import tinyagentos.routes.settings as settings_mod

UNIT = "taos-rk3588-perf.service"


class FakeSystemctl:
    """Records calls; answers is-enabled/is-active from mutable state and
    applies enable/disable to that state, like the real unit would."""

    def __init__(self, *, enabled=False, active=False, fail_on=None):
        self.enabled = enabled
        self.active = active
        self.fail_on = fail_on  # action ("enable"/"disable") to fail
        self.calls = []

    async def __call__(self, *args):
        self.calls.append(args)
        action = args[0]
        if action == "is-enabled":
            return (0, "") if self.enabled else (1, "")
        if action == "is-active":
            return (0, "") if self.active else (3, "")
        if action == self.fail_on:
            return 1, "simulated systemctl failure"
        if action == "enable":
            self.enabled = True
            self.active = True
        elif action == "disable":
            self.enabled = False
            self.active = False
        return 0, ""


@pytest.fixture
def perf_env(tmp_path, monkeypatch):
    """Point the perf probes at a tmp tree; return (unit_path, compat_path)."""
    unit_path = tmp_path / "etc" / "systemd" / "system" / UNIT
    compat_path = tmp_path / "proc" / "device-tree" / "compatible"
    monkeypatch.setattr(settings_mod, "_RK3588_PERF_UNIT_PATH", unit_path, raising=False)
    monkeypatch.setattr(settings_mod, "_RK3588_COMPATIBLE_PATH", compat_path, raising=False)
    return unit_path, compat_path


def _install_unit(unit_path):
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("[Unit]\nDescription=perf\n")


def _set_board(compat_path, value: bytes):
    compat_path.parent.mkdir(parents=True, exist_ok=True)
    compat_path.write_bytes(value)


@pytest.mark.asyncio
async def test_get_reports_unsupported_board_without_unit(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _set_board(compat_path, b"raspberrypi,5-model-b\x00brcm,bcm2712\x00")
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.get("/api/settings/rk3588-performance")

    assert resp.status_code == 200
    assert resp.json() == {
        "supported": False,
        "installed": False,
        "enabled": False,
        "active": False,
    }
    # No unit installed: systemctl is never consulted.
    assert fake.calls == []


@pytest.mark.asyncio
async def test_get_reports_state_on_rk3588_with_unit(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(enabled=True, active=True)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.get("/api/settings/rk3588-performance")

    assert resp.status_code == 200
    assert resp.json() == {
        "supported": True,
        "installed": True,
        "enabled": True,
        "active": True,
    }
    assert fake.calls == [("is-enabled", UNIT), ("is-active", UNIT)]


@pytest.mark.asyncio
async def test_get_reports_disabled_unit(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(enabled=False, active=False)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.get("/api/settings/rk3588-performance")

    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    assert body["installed"] is True
    assert body["enabled"] is False
    assert body["active"] is False


@pytest.mark.asyncio
async def test_put_enable_dispatches_enable_now(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(enabled=False, active=False)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["active"] is True
    assert ("enable", "--now", UNIT) in fake.calls


@pytest.mark.asyncio
async def test_put_disable_dispatches_disable_now(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(enabled=True, active=True)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": False})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["active"] is False
    assert ("disable", "--now", UNIT) in fake.calls


@pytest.mark.asyncio
async def test_put_systemctl_failure_is_502(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(fail_on="enable")
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})

    assert resp.status_code == 502
    assert "simulated systemctl failure" in resp.json()["error"]


@pytest.mark.asyncio
async def test_put_without_unit_is_409_and_dispatches_nothing(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})

    assert resp.status_code == 409
    assert fake.calls == []


@pytest.mark.asyncio
async def test_put_rejects_non_bool_enabled(client, perf_env, monkeypatch):
    unit_path, compat_path = perf_env
    _install_unit(unit_path)
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": "yes"})

    assert resp.status_code == 400
    assert fake.calls == []
