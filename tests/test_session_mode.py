"""Session-mode endpoint: the device gate and the input validation.

These cover the two answers that must NOT depend on a real systemd: a machine
without the kiosk unit must refuse the switch (so a desktop install never acts
on a phone-only control), and an unknown mode must be rejected before anything
is started.
"""
from __future__ import annotations

import pytest

from tinyagentos.routes import system as system_routes


@pytest.mark.asyncio
async def test_state_reports_unavailable_when_unit_absent(monkeypatch):
    """`systemctl cat` failing is what marks a device as non-handset.

    `is-active` answers "inactive" for an unknown unit just as it does for a
    known-but-stopped one, so availability MUST come from `cat`, not from it.
    """
    async def fake(*args):
        if args[0] == "cat":
            return 1, "No files found for taos-kiosk.service."
        return 3, "inactive"

    monkeypatch.setattr(system_routes, "_systemctl", fake)
    state = await system_routes._session_mode_state()
    assert state["available"] is False
    assert state["current"] == "unknown"


@pytest.mark.asyncio
async def test_state_reports_kiosk_when_kiosk_active(monkeypatch):
    async def fake(*args):
        if args[0] == "cat":
            return 0, "# taos-kiosk.service"
        unit = args[-1]
        return (0, "active") if unit == "taos-kiosk.service" else (3, "inactive")

    monkeypatch.setattr(system_routes, "_systemctl", fake)
    state = await system_routes._session_mode_state()
    assert state["available"] is True
    assert state["current"] == "kiosk"


@pytest.mark.asyncio
async def test_switch_refused_on_a_device_without_the_kiosk(monkeypatch):
    """A desktop/server install must 404 rather than shell out to systemctl."""
    started: list[str] = []

    async def fake(*args):
        if args[0] == "start":
            started.append(args[-1])
            return 0, ""
        if args[0] == "cat":
            return 1, "No files found"
        return 3, "inactive"

    monkeypatch.setattr(system_routes, "_systemctl", fake)

    class _Req:
        async def body(self):
            return b'{"mode":"plasma"}'

        async def json(self):
            return {"mode": "plasma"}

    resp = await system_routes.set_session_mode(_Req())
    assert resp.status_code == 404
    assert started == [], "must not start a unit on an unsupported device"


@pytest.mark.asyncio
async def test_unknown_mode_rejected_before_any_unit_is_touched(monkeypatch):
    started: list[str] = []

    async def fake(*args):
        if args[0] == "start":
            started.append(args[-1])
        return 0, "active"

    monkeypatch.setattr(system_routes, "_systemctl", fake)

    class _Req:
        async def body(self):
            return b'{"mode":"reboot-into-something"}'

        async def json(self):
            return {"mode": "reboot-into-something"}

    resp = await system_routes.set_session_mode(_Req())
    assert resp.status_code == 400
    assert started == []
