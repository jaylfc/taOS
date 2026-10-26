"""Hardware Bluetooth read route via BlueZ D-Bus (busctl)."""

from __future__ import annotations

import pytest

from tinyagentos.routes import hardware_bluetooth as bt_routes


@pytest.mark.asyncio
async def test_unavailable_when_bluetoothd_down(monkeypatch):
    """When busctl call fails (bluetoothd not running), return available=false with empty lists."""
    async def fake(*args):
        return 1, None

    monkeypatch.setattr(bt_routes, "busctl", fake)
    resp = await bt_routes.get_bluetooth()
    assert resp == {"available": False, "adapters": [], "devices": []}


@pytest.mark.asyncio
async def test_lists_adapter_and_sorted_devices(monkeypatch):
    """One adapter (Powered true) plus three devices sorted: connected first, then paired, then by name."""
    async def fake(*args):
        # One call: GetManagedObjects
        if args[0] == "call" and args[1] == "org.bluez" and args[2] == "/" and args[3] == "org.freedesktop.DBus.ObjectManager" and args[4] == "GetManagedObjects":
            return 0, [{
                "/org/bluez/hci0": {
                    "org.bluez.Adapter1": {
                        "Address": {"type": "s", "data": "AA:BB:CC:DD:EE:FF"},
                        "Powered": {"type": "b", "data": True},
                    }
                },
                "/org/bluez/hci0/dev_11_22_33_44_55_66": {
                    "org.bluez.Device1": {
                        "Address": {"type": "s", "data": "11:22:33:44:55:66"},
                        "Alias": {"type": "s", "data": "Connected Device"},
                        "Paired": {"type": "b", "data": True},
                        "Connected": {"type": "b", "data": True},
                        "Trusted": {"type": "b", "data": True},
                    }
                },
                "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {
                    "org.bluez.Device1": {
                        "Address": {"type": "s", "data": "AA:BB:CC:DD:EE:FF"},
                        "Alias": {"type": "s", "data": "Paired Device"},
                        "Paired": {"type": "b", "data": True},
                        "Connected": {"type": "b", "data": False},
                        "Trusted": {"type": "b", "data": False},
                    }
                },
                "/org/bluez/hci0/dev_99_88_77_66_55_44": {
                    "org.bluez.Device1": {
                        "Address": {"type": "s", "data": "99:88:77:66:55:44"},
                        "Name": {"type": "s", "data": "Unpaired Device"},
                        "Paired": {"type": "b", "data": False},
                        "Connected": {"type": "b", "data": False},
                        "Trusted": {"type": "b", "data": False},
                    }
                },
            }]
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(bt_routes, "busctl", fake)
    resp = await bt_routes.get_bluetooth()
    assert resp["available"] is True
    assert resp["adapters"] == [
        {"path": "/org/bluez/hci0", "address": "AA:BB:CC:DD:EE:FF", "powered": True}
    ]
    # Sorted: connected first, then paired, then by name
    assert resp["devices"] == [
        {
            "path": "/org/bluez/hci0/dev_11_22_33_44_55_66",
            "address": "11:22:33:44:55:66",
            "name": "Connected Device",
            "paired": True,
            "connected": True,
            "trusted": True,
        },
        {
            "path": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Paired Device",
            "paired": True,
            "connected": False,
            "trusted": False,
        },
        {
            "path": "/org/bluez/hci0/dev_99_88_77_66_55_44",
            "address": "99:88:77:66:55:44",
            "name": "Unpaired Device",
            "paired": False,
            "connected": False,
            "trusted": False,
        },
    ]


@pytest.mark.asyncio
async def test_zero_adapters_is_available(monkeypatch):
    """When busctl call succeeds but returns empty map, available=true with empty lists."""
    async def fake(*args):
        if args[0] == "call" and args[1] == "org.bluez" and args[2] == "/" and args[3] == "org.freedesktop.DBus.ObjectManager" and args[4] == "GetManagedObjects":
            return 0, [{}]
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(bt_routes, "busctl", fake)
    resp = await bt_routes.get_bluetooth()
    assert resp == {"available": True, "adapters": [], "devices": []}
