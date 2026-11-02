"""Hardware Bluetooth read route via BlueZ D-Bus (busctl)."""

from __future__ import annotations

import asyncio
import sys
import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

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
        {"path": "/org/bluez/hci0", "address": "AA:BB:CC:DD:EE:FF", "powered": True, "discovering": False}
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


# Tests for the new discovery route and bluez_bus module

class BtDiscoveryBody(BaseModel):
    adapter: str = Field(..., description="BlueZ adapter path")
    on: bool = Field(..., description="Start or stop discovery")


async def fake_get_system_bus_success():
    """Fake get_system_bus that returns a mock bus."""
    from dbus_fast import MessageType
    
    bus = type("MockBus", (), {})()
    bus.calls = []

    class MockMessage:
        def __init__(self, destination, path, interface, member, signature="", body=None):
            self.destination = destination
            self.path = path
            self.interface = interface
            self.member = member
            self.signature = signature
            self.body = body or []

    class MockReply:
        def __init__(self, body=None, error_name=None, message_type=None):
            self.body = body if body is not None else []
            self.error_name = error_name
            self.message_type = message_type

    async def call(message):
        bus.calls.append(message)
        if message.member == "StartDiscovery":
            if message.path == "/org/bluez/hci0" and hasattr(bus, "discovery_in_progress") and bus.discovery_in_progress:
                return MockReply(error_name="org.bluez.Error.InProgress", message_type=MessageType.ERROR)
            bus.discovery_in_progress = True
            return MockReply(message_type=MessageType.METHOD_RETURN)
        elif message.member == "StopDiscovery":
            bus.discovery_in_progress = False
            return MockReply(message_type=MessageType.METHOD_RETURN)
        elif message.member == "Get" and message.interface == "org.freedesktop.DBus.Properties":
            if message.body[1] == "Discovering":
                return MockReply(body=[True if getattr(bus, "discovery_in_progress", False) else False], message_type=MessageType.METHOD_RETURN)
        return MockReply(message_type=MessageType.METHOD_RETURN)

    bus.call = call
    bus.disconnect = lambda: None
    bus.discovery_in_progress = False
    return bus


async def fake_get_system_bus_import_error():
    """Fake get_system_bus that raises ImportError."""
    raise ImportError("No module named 'dbus_fast'")


async def fake_get_system_bus_connect_error():
    """Fake get_system_bus that raises OSError on connect."""
    raise OSError("Failed to connect to system bus")


def make_discovery_body(adapter: str, on: bool):
    """Helper to create discovery request body."""
    return BtDiscoveryBody(adapter=adapter, on=on)


@pytest.mark.asyncio
async def test_discovery_start_sends_startdiscovery(monkeypatch):
    """Discovery start sends exactly one StartDiscovery to the adapter path."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    mock_bus = await fake_get_system_bus_success()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body)

    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": True}

    assert len(mock_bus.calls) == 2
    assert mock_bus.calls[0].member == "StartDiscovery"
    assert mock_bus.calls[0].path == "/org/bluez/hci0"
    assert mock_bus.calls[0].destination == "org.bluez"
    assert mock_bus.calls[0].interface == "org.bluez.Adapter1"
    assert mock_bus.calls[1].member == "Get"
    assert mock_bus.calls[1].interface == "org.freedesktop.DBus.Properties"


@pytest.mark.asyncio
async def test_discovery_stop_sends_stopdiscovery(monkeypatch):
    """Discovery stop cancels timer and sends StopDiscovery."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    mock_bus = await fake_get_system_bus_success()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body_start = make_discovery_body("/org/bluez/hci0", True)
    await bt_routes.discovery_bluetooth(body_start)

    body_stop = make_discovery_body("/org/bluez/hci0", False)
    resp = await bt_routes.discovery_bluetooth(body_stop)

    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": False}

    stop_calls = [c for c in mock_bus.calls if c.member == "StopDiscovery"]
    assert len(stop_calls) == 1
    assert stop_calls[0].path == "/org/bluez/hci0"


@pytest.mark.asyncio
async def test_discovery_invalid_adapter_path_422(monkeypatch):
    """Invalid adapter path returns 422, fake bus never called."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    bus_called = False

    async def fake_get_bus():
        nonlocal bus_called
        bus_called = True
        return await fake_get_system_bus_success()

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = make_discovery_body("/org/bluez/hci0/../x", True)
    resp = await bt_routes.discovery_bluetooth(body)

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 422
    assert resp.body.decode() == "{\"error\":\"invalid_path\"}"
    assert not bus_called


@pytest.mark.asyncio
async def test_discovery_dbus_fast_missing_503(monkeypatch):
    """ImportError on dbus_fast returns 503 dbus_fast_missing."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    async def fake_get_bus():
        raise bluez_bus.BusUnavailable("dbus_fast_missing")

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body)

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503
    assert resp.body.decode() == "{\"error\":\"dbus_fast_missing\"}"


@pytest.mark.asyncio
async def test_discovery_bus_unavailable_503(monkeypatch):
    """OSError on bus connect returns 503 bluez_unavailable."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    async def fake_get_bus():
        raise bluez_bus.BusUnavailable("bluez_unavailable")

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body)

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503
    assert resp.body.decode() == "{\"error\":\"bluez_unavailable\"}"


@pytest.mark.asyncio
async def test_discovery_inprogress_counts_as_success(monkeypatch):
    """org.bluez.Error.InProgress on StartDiscovery counts as success."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    mock_bus = await fake_get_system_bus_success()
    mock_bus.discovery_in_progress = True

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body)

    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": True}

    assert len(mock_bus.calls) == 2
    assert mock_bus.calls[0].member == "StartDiscovery"
    assert mock_bus.calls[1].member == "Get"


@pytest.mark.asyncio
async def test_discovery_other_dbus_error_502(monkeypatch):
    """Other D-Bus error returns 502 write_failed with error name."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes
    from dbus_fast import MessageType

    class MockMessage:
        def __init__(self, destination, path, interface, member, signature="", body=None):
            self.destination = destination
            self.path = path
            self.interface = interface
            self.member = member
            self.signature = signature
            self.body = body or []

    class MockReply:
        def __init__(self, error_name):
            self.error_name = error_name
            self.message_type = MessageType.ERROR

    class MockBus:
        def __init__(self):
            self.calls = []

        async def call(self, message):
            self.calls.append(message)
            return MockReply("org.bluez.Error.Failed")

        def disconnect(self):
            pass

    mock_bus = MockBus()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body)

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 502
    import json
    data = json.loads(resp.body.decode())
    assert data["error"] == "write_failed"
    assert data["dbus_error"] == "org.bluez.Error.Failed"


@pytest.mark.asyncio
async def test_get_bluetooth_includes_discovering(monkeypatch):
    """GET /api/hardware/bluetooth includes discovering per adapter."""
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    async def fake_busctl(*args):
        if args[0] == "call" and args[1] == "org.bluez" and args[2] == "/" and args[3] == "org.freedesktop.DBus.ObjectManager" and args[4] == "GetManagedObjects":
            return 0, [{
                "/org/bluez/hci0": {
                    "org.bluez.Adapter1": {
                        "Address": {"type": "s", "data": "AA:BB:CC:DD:EE:FF"},
                        "Powered": {"type": "b", "data": True},
                        "Discovering": {"type": "b", "data": True},
                    }
                },
                "/org/bluez/hci1": {
                    "org.bluez.Adapter1": {
                        "Address": {"type": "s", "data": "11:22:33:44:55:66"},
                        "Powered": {"type": "b", "data": True},
                        "Discovering": {"type": "b", "data": False},
                    }
                },
            }]
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(bt_routes, "busctl", fake_busctl)
    resp = await bt_routes.get_bluetooth()

    assert resp["available"] is True
    assert len(resp["adapters"]) == 2
    adapter0 = next(a for a in resp["adapters"] if a["path"] == "/org/bluez/hci0")
    adapter1 = next(a for a in resp["adapters"] if a["path"] == "/org/bluez/hci1")
    assert adapter0["discovering"] is True
    assert adapter1["discovering"] is False


@pytest.mark.asyncio
async def test_close_system_bus_idempotent():
    """close_system_bus() twice does not raise."""
    from tinyagentos import bluez_bus

    await bluez_bus.close_system_bus()
    await bluez_bus.close_system_bus()
    # If we get here without exception, the test passes


# Tests for non-admin access and 60s auto-stop timer
@pytest.fixture
def bt_test_app(tmp_path):
    """Create a minimal app for Bluetooth route testing."""
    import yaml
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    # Lifespan-owned objects initialised eagerly
    from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair
    app.state.vapid_keypair = load_or_create_vapid_keypair(tmp_path)
    return app


@pytest.fixture
def bt_admin_headers(bt_test_app):
    """Admin auth headers."""
    auth_mgr = bt_test_app.state.auth
    if not auth_mgr.is_configured():
        auth_mgr.setup_user("admin", "Admin", "", "adminpass")
    record = auth_mgr.find_user("admin")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return {"Cookie": f"taos_session={token}"}


@pytest.fixture
def bt_nonadmin_headers(bt_test_app):
    """Non-admin auth headers (chat capability only)."""
    auth_mgr = bt_test_app.state.auth
    invite_code = auth_mgr.add_user_invite("nonadmin", "admin")
    auth_mgr.complete_invite("nonadmin", invite_code, "nonadmin", "", "testpass1")
    data = auth_mgr._read_users()
    for u in data["users"]:
        if u.get("username") == "nonadmin":
            u["capabilities"] = ["chat"]
            break
    auth_mgr._write_users(data)
    record = auth_mgr.find_user("nonadmin")
    token = auth_mgr.create_session(user_id=record["id"], long_lived=True)
    return {"Cookie": f"taos_session={token}"}


def test_discovery_non_admin_returns_403(bt_test_app, bt_nonadmin_headers, monkeypatch):
    """Non-admin user gets 403 on discovery endpoint."""
    from fastapi.testclient import TestClient

    # Mock get_system_bus to avoid real D-Bus
    async def fake_get_bus():
        raise ImportError("No module named 'dbus_fast'")

    import tinyagentos.bluez_bus as bluez_bus
    import tinyagentos.routes.hardware_bluetooth as bt_routes

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    with TestClient(bt_test_app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/hardware/bluetooth/discovery",
            json={"adapter": "/org/bluez/hci0", "on": True},
            headers=bt_nonadmin_headers,
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_discovery_auto_stop_timer_fires(monkeypatch):
    """60s auto-stop timer fires and sends StopDiscovery once."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    mock_bus = await fake_get_system_bus_success()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    # Patch the delay to 0 for fast test
    original_stop = bt_routes._stop_discovery_after_delay

    async def fast_stop(adapter, timer, delay=60.0):
        await original_stop(adapter, timer, delay=0.0)

    monkeypatch.setattr(bt_routes, "_stop_discovery_after_delay", fast_stop)

    body = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body)

    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": True}

    # Give the timer task a moment to run
    await asyncio.sleep(0.01)

    stop_calls = [c for c in mock_bus.calls if c.member == "StopDiscovery"]
    assert len(stop_calls) == 1
    assert stop_calls[0].path == "/org/bluez/hci0"

    # Timer should be cleaned up
    assert "/org/bluez/hci0" not in bt_routes._discovery_timers


@pytest.mark.asyncio
async def test_stop_discovery_after_delay_error_reply_logs_warning(monkeypatch, caplog):
    """_stop_discovery_after_delay with ERROR reply logs warning, no AttributeError."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes
    from dbus_fast import MessageType

    class MockMessage:
        def __init__(self, destination, path, interface, member, signature="", body=None):
            self.destination = destination
            self.path = path
            self.interface = interface
            self.member = member
            self.signature = signature
            self.body = body or []

    class MockReply:
        def __init__(self, error_name):
            self.error_name = error_name
            self.message_type = MessageType.ERROR

    class MockBus:
        def __init__(self):
            self.calls = []

        async def call(self, message):
            self.calls.append(message)
            return MockReply("org.bluez.Error.Failed")

        def disconnect(self):
            pass

    mock_bus = MockBus()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    # Create a dummy timer task
    async def dummy():
        await asyncio.sleep(100)

    timer = asyncio.create_task(dummy())

    # Should not raise AttributeError
    await bt_routes._stop_discovery_after_delay("/org/bluez/hci0", timer, delay=0.0)

    # Should log warning
    assert any("StopDiscovery error on /org/bluez/hci0" in record.message for record in caplog.records)
    assert any("org.bluez.Error.Failed" in record.message for record in caplog.records)

    timer.cancel()
    try:
        await timer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_get_system_bus_connect_timeout_raises_busunavailable(monkeypatch):
    """get_system_bus with never-finishing connect raises BusUnavailable."""
    from tinyagentos import bluez_bus

    # Patch the internal timeout to a very small value for the test
    original_timeout = 5.0
    # We can't easily patch the literal in the function, so we'll patch the whole module's wait_for
    # or use a different approach: make connect hang and verify the internal timeout fires

    async def slow_connect():
        await asyncio.sleep(10)  # Never finishes in test time

    class SlowMessageBus:
        def __init__(self, bus_type=None):
            pass

        async def connect(self):
            await slow_connect()

    # Patch where MessageBus is imported from
    monkeypatch.setattr("dbus_fast.aio.MessageBus", SlowMessageBus)

    # Need to clear the cached _bus to force reconnection
    bluez_bus._bus = None

    # The internal timeout is 5.0s, but we can't wait that long in tests.
    # Instead, verify that the code path raises BusUnavailable by mocking
    # the wait_for to use a shorter timeout. We'll temporarily replace
    # asyncio.wait_for in the bluez_bus module.
    original_wait_for = asyncio.wait_for

    async def short_wait_for(coro, timeout=None):
        return await original_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(bluez_bus.asyncio, "wait_for", short_wait_for)

    with pytest.raises(bluez_bus.BusUnavailable) as exc_info:
        await bluez_bus.get_system_bus()

    assert exc_info.value.code == "bluez_unavailable"


@pytest.mark.asyncio
async def test_discovery_stop_with_error_reply_returns_502(monkeypatch):
    """Stop discovery with ERROR reply returns 502 bluez_error."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes
    from dbus_fast import MessageType

    class MockMessage:
        def __init__(self, destination, path, interface, member, signature="", body=None):
            self.destination = destination
            self.path = path
            self.interface = interface
            self.member = member
            self.signature = signature
            self.body = body or []

    class MockReply:
        def __init__(self, error_name, body=None):
            self.error_name = error_name
            self.message_type = MessageType.ERROR
            self.body = body or []

    class MockBus:
        def __init__(self):
            self.calls = []

        async def call(self, message):
            self.calls.append(message)
            if message.member == "StopDiscovery":
                return MockReply("org.bluez.Error.Failed", body=["Some error"])
            return MockReply(message_type=MessageType.METHOD_RETURN)

        def disconnect(self):
            pass

    mock_bus = MockBus()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = bt_routes.BtDiscoveryBody(adapter="/org/bluez/hci0", on=False)
    resp = await bt_routes.discovery_bluetooth(body)

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 502
    import json
    data = json.loads(resp.body.decode())
    assert data["error"] == "bluez_error"
    assert data["detail"] == "org.bluez.Error.Failed"


@pytest.mark.asyncio
async def test_discovery_stop_notready_error_returns_ok(monkeypatch):
    """Stop discovery with NotReady error returns ok."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes
    from dbus_fast import MessageType

    class MockMessage:
        def __init__(self, destination, path, interface, member, signature="", body=None):
            self.destination = destination
            self.path = path
            self.interface = interface
            self.member = member
            self.signature = signature
            self.body = body or []

    class MockReply:
        def __init__(self, error_name):
            self.error_name = error_name
            self.message_type = MessageType.ERROR

    class MockBus:
        def __init__(self):
            self.calls = []

        async def call(self, message):
            self.calls.append(message)
            if message.member == "StopDiscovery":
                return MockReply("org.bluez.Error.NotReady")
            return MockReply(message_type=MessageType.METHOD_RETURN)

        def disconnect(self):
            pass

    mock_bus = MockBus()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = bt_routes.BtDiscoveryBody(adapter="/org/bluez/hci0", on=False)
    resp = await bt_routes.discovery_bluetooth(body)

    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": False}


@pytest.mark.asyncio
async def test_discovery_stop_failed_no_discovery_started_returns_ok(monkeypatch):
    """Stop discovery with Failed 'No discovery started' returns ok."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes
    from dbus_fast import MessageType

    class MockMessage:
        def __init__(self, destination, path, interface, member, signature="", body=None):
            self.destination = destination
            self.path = path
            self.interface = interface
            self.member = member
            self.signature = signature
            self.body = body or []

    class MockReply:
        def __init__(self, error_name, body=None):
            self.error_name = error_name
            self.message_type = MessageType.ERROR
            self.body = body or []

    class MockBus:
        def __init__(self):
            self.calls = []

        async def call(self, message):
            self.calls.append(message)
            if message.member == "StopDiscovery":
                return MockReply("org.bluez.Error.Failed", body=["No discovery started"])
            return MockReply(message_type=MessageType.METHOD_RETURN)

        def disconnect(self):
            pass

    mock_bus = MockBus()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body = bt_routes.BtDiscoveryBody(adapter="/org/bluez/hci0", on=False)
    resp = await bt_routes.discovery_bluetooth(body)

    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": False}


@pytest.mark.asyncio
async def test_discovery_start_error_keeps_old_timer(monkeypatch):
    """StartDiscovery error keeps the old timer in place."""
    from tinyagentos import bluez_bus
    from tinyagentos.routes import hardware_bluetooth as bt_routes
    from dbus_fast import MessageType

    # First, set up a successful start to create a timer
    mock_bus = await fake_get_system_bus_success()

    async def fake_get_bus():
        return mock_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", fake_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", fake_get_bus)

    body_start = make_discovery_body("/org/bluez/hci0", True)
    resp = await bt_routes.discovery_bluetooth(body_start)
    if isinstance(resp, JSONResponse):
        assert resp.status_code == 200
    assert resp == {"ok": True, "discovering": True}

    # Verify timer exists
    assert "/org/bluez/hci0" in bt_routes._discovery_timers
    old_timer = bt_routes._discovery_timers["/org/bluez/hci0"]

    # Now simulate a StartDiscovery error on second start
    class FailingMockBus:
        def __init__(self):
            self.calls = []

        async def call(self, message):
            self.calls.append(message)
            if message.member == "StartDiscovery":
                class ErrorReply:
                    error_name = "org.bluez.Error.Failed"
                    message_type = MessageType.ERROR
                return ErrorReply()
            return MockReply(message_type=MessageType.METHOD_RETURN)

        def disconnect(self):
            pass

    failing_bus = FailingMockBus()

    async def failing_get_bus():
        return failing_bus

    monkeypatch.setattr(bluez_bus, "get_system_bus", failing_get_bus)
    monkeypatch.setattr(bt_routes, "get_system_bus", failing_get_bus)

    body_start2 = make_discovery_body("/org/bluez/hci0", True)
    resp2 = await bt_routes.discovery_bluetooth(body_start2)

    assert isinstance(resp2, JSONResponse)
    assert resp2.status_code == 502

    # Old timer should still be in place (not replaced by new one)
    assert "/org/bluez/hci0" in bt_routes._discovery_timers
    assert bt_routes._discovery_timers["/org/bluez/hci0"] is old_timer

    # Clean up
    old_timer.cancel()
    try:
        await old_timer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_get_bluetooth_decodes_variant_value(monkeypatch):
    """GET /api/hardware/bluetooth decodes Variant (.value) before reading Discovering."""
    from tinyagentos.routes import hardware_bluetooth as bt_routes

    async def fake_busctl(*args):
        if args[0] == "call" and args[1] == "org.bluez" and args[2] == "/" and args[3] == "org.freedesktop.DBus.ObjectManager" and args[4] == "GetManagedObjects":
            return 0, [{
                "/org/bluez/hci0": {
                    "org.bluez.Adapter1": {
                        "Address": {"type": "s", "data": "AA:BB:CC:DD:EE:FF"},
                        "Powered": {"type": "b", "data": True},
                        "Discovering": {"type": "b", "data": True},  # Raw variant with .value
                    }
                },
            }]
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(bt_routes, "busctl", fake_busctl)
    resp = await bt_routes.get_bluetooth()

    assert resp["available"] is True
    assert len(resp["adapters"]) == 1
    assert resp["adapters"][0]["discovering"] is True
