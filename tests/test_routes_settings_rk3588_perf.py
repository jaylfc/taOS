"""RK3588 performance-mode settings routes (#361).

The Settings toggle drives the taos-rk3588-perf.service unit that
install-server.sh installs on Rockchip boards: GET reports board support,
unit presence, the persisted toggle flag and the unit's active state;
PUT starts/stops the unit for the immediate governor change and flips
the flag the unit gates its boot start on. The toggle never changes the
unit's enablement — systemd's unit-files polkit action carries no
unit detail a rule could scope by (see the polkit rule file).
systemctl is faked at the routes.settings._systemctl seam and the three
filesystem probes (unit file, device-tree compatible, toggle flag) are
pointed at tmp paths so a test never touches the machine's real /etc,
/proc or /var.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

import tinyagentos.routes.settings as settings_mod

UNIT = "taos-rk3588-perf.service"


class FakeSystemctl:
    """Records calls; answers is-active from mutable state and applies
    start/stop to that state, like the real unit would. Any other verb
    answers 1, so a route that (re)grows an enable/disable call fails
    its test as a 502 instead of silently succeeding.

    With flag_path given, start also models the unit's
    ConditionPathExists gate: the flag's presence is recorded, and a
    start with the flag absent is skipped (success, unit stays
    inactive) exactly as systemd skips a condition-failed start."""

    def __init__(self, *, active=False, fail_on=None, flag_path=None):
        self.active = active
        self.fail_on = fail_on  # action ("start"/"stop") to fail
        self.flag_path = flag_path
        self.calls = []
        self.flag_present_at_start = None

    async def __call__(self, *args):
        self.calls.append(args)
        action = args[0]
        if action == "is-active":
            return (0, "") if self.active else (3, "")
        if action == "start" and self.flag_path is not None:
            self.flag_present_at_start = self.flag_path.exists()
        if action == self.fail_on:
            return 1, "simulated systemctl failure"
        if action == "start":
            if self.flag_path is not None and not self.flag_path.exists():
                return 0, ""
            self.active = True
        elif action == "stop":
            self.active = False
        else:
            return 1, f"unexpected systemctl action: {action}"
        return 0, ""


@pytest.fixture
def perf_env(tmp_path, monkeypatch):
    """Point the perf probes at a tmp tree; return (unit, compat, flag)."""
    unit_path = tmp_path / "etc" / "systemd" / "system" / UNIT
    compat_path = tmp_path / "proc" / "device-tree" / "compatible"
    flag_path = tmp_path / "var" / "lib" / "taos-rk3588-perf" / "enabled"
    monkeypatch.setattr(settings_mod, "_RK3588_PERF_UNIT_PATH", unit_path, raising=False)
    monkeypatch.setattr(settings_mod, "_RK3588_COMPATIBLE_PATH", compat_path, raising=False)
    monkeypatch.setattr(settings_mod, "_RK3588_PERF_FLAG_PATH", flag_path, raising=False)
    return unit_path, compat_path, flag_path


def _install_unit(unit_path):
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text("[Unit]\nDescription=perf\n")


def _set_flag(flag_path):
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text("")


def _set_board(compat_path, value: bytes):
    compat_path.parent.mkdir(parents=True, exist_ok=True)
    compat_path.write_bytes(value)


@pytest.mark.asyncio
async def test_get_reports_unsupported_board_without_unit(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
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
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    _set_flag(flag_path)
    fake = FakeSystemctl(active=True)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.get("/api/settings/rk3588-performance")

    assert resp.status_code == 200
    assert resp.json() == {
        "supported": True,
        "installed": True,
        "enabled": True,
        "active": True,
    }
    assert fake.calls == [("is-active", UNIT)]


@pytest.mark.asyncio
async def test_get_reports_mode_off_when_flag_absent(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(active=False)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.get("/api/settings/rk3588-performance")

    assert resp.status_code == 200
    body = resp.json()
    assert body["supported"] is True
    assert body["installed"] is True
    assert body["enabled"] is False
    assert body["active"] is False


@pytest.mark.asyncio
async def test_put_enable_starts_unit_and_sets_flag(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(active=False, flag_path=flag_path)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["active"] is True
    assert ("start", UNIT) in fake.calls
    assert flag_path.exists()
    # The flag must exist before the start is issued: the unit gates
    # its start on it, so a start issued first is silently skipped.
    assert fake.flag_present_at_start is True


@pytest.mark.asyncio
async def test_put_disable_stops_unit_and_clears_flag(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    _set_flag(flag_path)
    fake = FakeSystemctl(active=True)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": False})

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["active"] is False
    assert ("stop", UNIT) in fake.calls
    assert not flag_path.exists()


@pytest.mark.asyncio
async def test_put_systemctl_failure_is_502(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl(fail_on="start", flag_path=flag_path)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})

    assert resp.status_code == 502
    assert "simulated systemctl failure" in resp.json()["error"]
    # The flag is created before the start (the unit's start gates on
    # it) and rolled back when the start fails: nothing persists.
    assert fake.flag_present_at_start is True
    assert not flag_path.exists()


@pytest.mark.asyncio
async def test_put_enable_after_disable_reactivates_unit(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    _set_flag(flag_path)
    fake = FakeSystemctl(active=True, flag_path=flag_path)
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["active"] is False
    assert not flag_path.exists()

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    # Not just persisted: the unit must actually run again. A start
    # issued before the flag is recreated is condition-skipped by
    # systemd and the unit would stay inactive until the next boot.
    assert body["active"] is True
    assert fake.flag_present_at_start is True


@pytest.mark.asyncio
async def test_put_without_unit_is_409_and_dispatches_nothing(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": True})

    assert resp.status_code == 409
    assert fake.calls == []
    assert not flag_path.exists()


@pytest.mark.asyncio
async def test_put_rejects_non_bool_enabled(client, perf_env, monkeypatch):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    resp = await client.put("/api/settings/rk3588-performance", json={"enabled": "yes"})

    assert resp.status_code == 400
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Non-admin refusal (jaylfc, PR #4506): the toggle is admin-only. A
# signed-in member session is refused by the settings router's admin
# gate with 403; an agent's registry JWT is refused even earlier --
# AuthMiddleware only admits registry JWTs on their allowlisted routes
# and answers 401 elsewhere (anti-enumeration), so the toggle never
# sees it. Both callers must dispatch no systemctl call.
# ---------------------------------------------------------------------------


async def _member_client(app) -> AsyncClient:
    """CSRF-armed client for a non-admin member session on *app*.

    Mirrors tests/test_settings_authz.py's helper; the CSRF hooks make
    the client behave like the real SPA so the request reaches the
    admin gate itself instead of stopping at the CSRF check (both
    answer 403, but only the gate is under test here). Requires the
    admin created by the ``client`` fixture.
    """
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("perfmember", "admin")
    auth_mgr.complete_invite(
        "perfmember", invite_code, "Perf Member", "", "perfmemberpass123"
    )
    member = auth_mgr.find_user("perfmember")
    token = auth_mgr.create_session(member["id"])
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


@pytest.mark.asyncio
async def test_put_member_session_is_403_and_dispatches_nothing(
    client, app, perf_env, monkeypatch
):
    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    member_client = await _member_client(app)
    try:
        resp = await member_client.put(
            "/api/settings/rk3588-performance", json={"enabled": True}
        )
    finally:
        await member_client.aclose()

    assert resp.status_code == 403
    assert fake.calls == []
    assert not flag_path.exists()


@pytest.mark.asyncio
async def test_put_agent_token_is_refused_and_dispatches_nothing(
    client, app, perf_env, monkeypatch
):
    from tinyagentos.agent_registry_store import mint_registry_token

    unit_path, compat_path, flag_path = perf_env
    _install_unit(unit_path)
    _set_board(compat_path, b"rockchip,rk3588\x00")
    fake = FakeSystemctl()
    monkeypatch.setattr(settings_mod, "_systemctl", fake)

    # A genuine agent credential: a registered agent's registry JWT.
    registry = app.state.agent_registry
    await registry.init()
    owner = app.state.auth.find_user("admin")["id"]
    rec = await registry.register(
        framework="hermes", display_name="perf-agent", user_id=owner
    )
    priv, _pub = app.state.agent_registry_keypair
    token = mint_registry_token(rec["canonical_id"], priv, user_id=owner)

    bare_client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )
    try:
        resp = await bare_client.put(
            "/api/settings/rk3588-performance",
            json={"enabled": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        await bare_client.aclose()

    # Refused at the middleware (401: registry JWTs are allowlist-only
    # and this route is not on it) -- never reaches the toggle handler.
    assert resp.status_code == 401
    assert fake.calls == []
    assert not flag_path.exists()
