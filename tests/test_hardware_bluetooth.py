"""Hardware Bluetooth read route via BlueZ D-Bus (busctl)."""

from __future__ import annotations

import pytest
from fastapi.responses import JSONResponse

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


# Tests for the new power route
async def fake_busctl_power_success(*args):
    """Fake busctl that succeeds for power operations."""
    if args[0] == "set-property" and args[1] == "org.bluez" and args[3] == "org.bluez.Adapter1" and args[4] == "Powered":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_power_unavailable(*args):
    """Fake busctl that returns rc=127 (unavailable)."""
    if args[0] == "set-property" and args[1] == "org.bluez" and args[3] == "org.bluez.Adapter1" and args[4] == "Powered":
        return 127, None
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_power_timeout(*args):
    """Fake busctl that returns rc=124 (timeout)."""
    if args[0] == "set-property" and args[1] == "org.bluez" and args[3] == "org.bluez.Adapter1" and args[4] == "Powered":
        return 124, None
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_power_failed(*args):
    """Fake busctl that returns rc=1 (other failure)."""
    if args[0] == "set-property" and args[1] == "org.bluez" and args[3] == "org.bluez.Adapter1" and args[4] == "Powered":
        return 1, None
    raise AssertionError(f"unexpected busctl call: {args}")


@pytest.mark.asyncio
async def test_power_bluetooth_adapter(monkeypatch):
    """Power Bluetooth adapter route issues correct busctl call."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_power_success)
    body = bt_routes.BtPowerBody(adapter="/org/bluez/hci0", powered=True)
    resp = await bt_routes.power_bluetooth_adapter(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
        assert resp.body.decode() == b'{"ok": true}'
    else:
        assert resp == {"ok": True}


@pytest.mark.asyncio
async def test_power_bluetooth_adapter_unavailable(monkeypatch):
    """Power Bluetooth adapter route returns 503 when bluez unavailable."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_power_unavailable)
    body = bt_routes.BtPowerBody(adapter="/org/bluez/hci0", powered=False)
    resp = await bt_routes.power_bluetooth_adapter(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 503
        assert resp.body.decode() == "{\"error\":\"bluez_unavailable\"}"
    else:
        assert resp == {"error": "bluez_unavailable"}


@pytest.mark.asyncio
async def test_power_bluetooth_adapter_timeout(monkeypatch):
    """Power Bluetooth adapter route returns 504 when timeout."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_power_timeout)
    body = bt_routes.BtPowerBody(adapter="/org/bluez/hci0", powered=True)
    resp = await bt_routes.power_bluetooth_adapter(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 504
        assert resp.body.decode() == "{\"error\":\"timeout\"}"
    else:
        assert resp == {"error": "timeout"}


@pytest.mark.asyncio
async def test_power_bluetooth_adapter_write_failed(monkeypatch):
    """Power Bluetooth adapter route returns 502 when write failed."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_power_failed)
    body = bt_routes.BtPowerBody(adapter="/org/bluez/hci0", powered=False)
    resp = await bt_routes.power_bluetooth_adapter(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 502
        assert resp.body.decode() == "{\"error\":\"write_failed\"}"
    else:
        assert resp == {"error": "write_failed"}


@pytest.mark.asyncio
async def test_power_bluetooth_adapter_invalid_path(monkeypatch):
    """Power Bluetooth adapter route returns 422 when path invalid."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_power_success)
    body = bt_routes.BtPowerBody(adapter="/invalid/path", powered=True)
    resp = await bt_routes.power_bluetooth_adapter(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 422
        assert resp.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp == {"error": "invalid_path"}


@pytest.mark.asyncio
async def test_power_route_injection_attempts(monkeypatch):
    """Power route returns 422 for injection attempts."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_power_success)

    # Test with adapter path that looks like a command
    body1 = bt_routes.BtPowerBody(adapter="/org/bluez/hci0; rm", powered=True)
    resp1 = await bt_routes.power_bluetooth_adapter(body1)
    if isinstance(resp1, JSONResponse):
        assert resp1.status_code == 422
        assert resp1.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp1 == {"error": "invalid_path"}

    # Test with adapter path that looks like a flag
    body2 = bt_routes.BtPowerBody(adapter="/org/bluez/hci0--help", powered=True)
    resp2 = await bt_routes.power_bluetooth_adapter(body2)
    if isinstance(resp2, JSONResponse):
        assert resp2.status_code == 422
        assert resp2.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp2 == {"error": "invalid_path"}

    # Test with adapter path that tries to escape
    body3 = bt_routes.BtPowerBody(adapter="/org/bluez/hci0/../x", powered=True)
    resp3 = await bt_routes.power_bluetooth_adapter(body3)
    if isinstance(resp3, JSONResponse):
        assert resp3.status_code == 422
        assert resp3.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp3 == {"error": "invalid_path"}


# Tests for the new device route
async def fake_busctl_device_connect(*args):
    """Fake busctl that succeeds for device connect operation."""
    if args[0] == "call" and args[1] == "org.bluez" and args[3] == "org.bluez.Device1" and args[4] == "Connect":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_disconnect(*args):
    """Fake busctl that succeeds for device disconnect operation."""
    if args[0] == "call" and args[1] == "org.bluez" and args[3] == "org.bluez.Device1" and args[4] == "Disconnect":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_pair(*args):
    """Fake busctl that succeeds for device pair operation."""
    if args[0] == "call" and args[1] == "org.bluez" and args[3] == "org.bluez.Device1" and args[4] == "Pair":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_trust(*args):
    """Fake busctl that succeeds for device trust operation."""
    if args[0] == "set-property" and args[1] == "org.bluez" and args[3] == "org.bluez.Device1" and args[4] == "Trusted":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_untrust(*args):
    """Fake busctl that succeeds for device untrust operation."""
    if args[0] == "set-property" and args[1] == "org.bluez" and args[3] == "org.bluez.Device1" and args[4] == "Trusted":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_remove(*args):
    """Fake busctl that succeeds for device remove operation."""
    if args[0] == "call" and args[1] == "org.bluez" and args[3] == "org.bluez.Adapter1" and args[4] == "RemoveDevice":
        return 0, {"data": None}
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_unavailable(*args):
    """Fake busctl that returns rc=127 for device operations."""
    if args[0] in ("call", "set-property") and args[1] == "org.bluez":
        return 127, None
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_timeout(*args):
    """Fake busctl that returns rc=124 for device operations."""
    if args[0] in ("call", "set-property") and args[1] == "org.bluez":
        return 124, None
    raise AssertionError(f"unexpected busctl call: {args}")


async def fake_busctl_device_failed(*args):
    """Fake busctl that returns rc=1 for device operations."""
    if args[0] in ("call", "set-property") and args[1] == "org.bluez":
        return 1, None
    raise AssertionError(f"unexpected busctl call: {args}")


@pytest.mark.asyncio
async def test_device_bluetooth_actions(monkeypatch):
    """Device route issues correct busctl call for each action."""
    test_cases = [
        ("connect", fake_busctl_device_connect, "call", "org.bluez", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "org.bluez.Device1", "Connect"),
        ("disconnect", fake_busctl_device_disconnect, "call", "org.bluez", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "org.bluez.Device1", "Disconnect"),
        ("pair", fake_busctl_device_pair, "call", "org.bluez", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "org.bluez.Device1", "Pair"),
        ("trust", fake_busctl_device_trust, "set-property", "org.bluez", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "org.bluez.Device1", "Trusted", "b", "true"),
        ("untrust", fake_busctl_device_untrust, "set-property", "org.bluez", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "org.bluez.Device1", "Trusted", "b", "false"),
        ("remove", fake_busctl_device_remove, "call", "org.bluez", "/org/bluez/hci0", "org.bluez.Adapter1", "RemoveDevice", "o", "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"),
    ]

    for action, fake_func, expected_call_type, *expected_args in test_cases:
        monkeypatch.setattr(bt_routes, "busctl", fake_func)
        body = bt_routes.BtDeviceBody(
            path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            action=action
        )
        resp = await bt_routes.device_bluetooth_action(body)
        if isinstance(resp, JSONResponse):
            assert resp.status_code == 200
            assert resp.body.decode() == b'{"ok": true}'
        else:
            assert resp == {"ok": True}


@pytest.mark.asyncio
async def test_device_bluetooth_action_unavailable(monkeypatch):
    """Device route returns 503 when bluez unavailable."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_device_unavailable)
    body = bt_routes.BtDeviceBody(path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", action="connect")
    resp = await bt_routes.device_bluetooth_action(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 503
        assert resp.body.decode() == "{\"error\":\"bluez_unavailable\"}"
    else:
        assert resp == {"error": "bluez_unavailable"}


@pytest.mark.asyncio
async def test_device_bluetooth_action_timeout(monkeypatch):
    """Device route returns 504 when timeout."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_device_timeout)
    body = bt_routes.BtDeviceBody(path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", action="connect")
    resp = await bt_routes.device_bluetooth_action(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 504
        assert resp.body.decode() == "{\"error\":\"timeout\"}"
    else:
        assert resp == {"error": "timeout"}


@pytest.mark.asyncio
async def test_device_bluetooth_action_write_failed(monkeypatch):
    """Device route returns 502 when write failed."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_device_failed)
    body = bt_routes.BtDeviceBody(path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", action="connect")
    resp = await bt_routes.device_bluetooth_action(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 502
        assert resp.body.decode() == "{\"error\":\"write_failed\"}"
    else:
        assert resp == {"error": "write_failed"}


@pytest.mark.asyncio
async def test_device_bluetooth_action_invalid_path(monkeypatch):
    """Device route returns 422 when path invalid."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_device_connect)
    # Test with invalid path
    body = bt_routes.BtDeviceBody(path="/invalid/path", action="connect")
    resp = await bt_routes.device_bluetooth_action(body)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 422
        assert resp.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp == {"error": "invalid_path"}


@pytest.mark.asyncio
async def test_device_bluetooth_action_injection_attempts(monkeypatch):
    """Device route returns 422 for injection attempts."""
    monkeypatch.setattr(bt_routes, "busctl", fake_busctl_device_connect)

    # Test with path that looks like a command
    body1 = bt_routes.BtDeviceBody(path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF; rm", action="connect")
    resp1 = await bt_routes.device_bluetooth_action(body1)
    if isinstance(resp1, JSONResponse):
        assert resp1.status_code == 422
        assert resp1.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp1 == {"error": "invalid_path"}

    # Test with path that looks like a flag
    body2 = bt_routes.BtDeviceBody(path="/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF--help", action="connect")
    resp2 = await bt_routes.device_bluetooth_action(body2)
    if isinstance(resp2, JSONResponse):
        assert resp2.status_code == 422
        assert resp2.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp2 == {"error": "invalid_path"}

    # Test with adapter path that tries to escape
    body3 = bt_routes.BtDeviceBody(path="/org/bluez/hci0/../x/dev_AA_BB_CC_DD_EE_FF", action="connect")
    resp3 = await bt_routes.device_bluetooth_action(body3)
    if isinstance(resp3, JSONResponse):
        assert resp3.status_code == 422
        assert resp3.body.decode() == "{\"error\":\"invalid_path\"}"
    else:
        assert resp3 == {"error": "invalid_path"}
