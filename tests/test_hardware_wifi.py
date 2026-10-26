"""Hardware Wi-Fi read route via NetworkManager D-Bus (busctl)."""

from __future__ import annotations

import json
import sys

import pytest

from tinyagentos.routes import hardware_wifi as hw_routes


def _json_body(resp) -> dict:
    """Extract JSON body from a JSONResponse or dict."""
    if isinstance(resp, dict):
        return resp
    return json.loads(resp.body.decode())


@pytest.mark.asyncio
async def test_unavailable_when_networkmanager_absent(monkeypatch):
    calls = []

    async def fake(*args):
        calls.append(args)
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 1, None
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)
    resp = await hw_routes.get_wifi()
    assert resp == {"available": False, "wifi_enabled": None, "devices": [], "access_points": []}
    assert calls == [("get-property", "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager", "org.freedesktop.NetworkManager", "WirelessEnabled")]


@pytest.mark.asyncio
async def test_lists_wifi_aps_sorted_and_marks_active(monkeypatch):
    calls = []

    async def fake(*args):
        calls.append(args)
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "call" and args[-1] == "GetDevices":
            return 0, ["/org/freedesktop/NetworkManager/Devices/1", "/org/freedesktop/NetworkManager/Devices/2"]
        # device 1 is ethernet (type 1)
        if args[0] == "get-property" and args[1] == "org.freedesktop.NetworkManager" and args[2] == "/org/freedesktop/NetworkManager/Devices/1" and args[4] == "DeviceType":
            return 0, 1
        # device 2 is wifi (type 2)
        if args[0] == "get-property" and args[1] == "org.freedesktop.NetworkManager" and args[2] == "/org/freedesktop/NetworkManager/Devices/2":
            if args[4] == "DeviceType":
                return 0, 2
            if args[4] == "Interface":
                return 0, "wlan0"
            if args[4] == "State":
                return 0, 100
        # wifi device active ap
        if args[0] == "get-property" and args[1] == "org.freedesktop.NetworkManager" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[3] == "org.freedesktop.NetworkManager.Device.Wireless" and args[4] == "ActiveAccessPoint":
            return 0, "/org/freedesktop/NetworkManager/AccessPoint/10"
        # get all aps
        if args[0] == "call" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[3] == "org.freedesktop.NetworkManager.Device.Wireless" and args[4] == "GetAllAccessPoints":
            return 0, ["/org/freedesktop/NetworkManager/AccessPoint/5", "/org/freedesktop/NetworkManager/AccessPoint/10"]
        # ap 5: cafe, strength 80, no security
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/AccessPoint/5":
            if args[4] == "Ssid":
                return 0, [ord(c) for c in "cafe"]
            if args[4] == "Strength":
                return 0, 80
            if args[4] == "Frequency":
                return 0, 2412
            if args[4] == "WpaFlags":
                return 0, 0
            if args[4] == "RsnFlags":
                return 0, 0
        # ap 10: home, strength 40, secured
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/AccessPoint/10":
            if args[4] == "Ssid":
                return 0, [ord(c) for c in "home"]
            if args[4] == "Strength":
                return 0, 40
            if args[4] == "Frequency":
                return 0, 5180
            if args[4] == "WpaFlags":
                return 0, 0
            if args[4] == "RsnFlags":
                return 0, 0x188
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)
    resp = await hw_routes.get_wifi()
    assert resp["available"] is True
    assert resp["wifi_enabled"] is True
    assert resp["devices"] == [{"path": "/org/freedesktop/NetworkManager/Devices/2", "iface": "wlan0", "state": 100}]
    assert resp["access_points"] == [
        {"ssid": "cafe", "strength": 80, "frequency": 2412, "secured": False, "active": False},
        {"ssid": "home", "strength": 40, "frequency": 5180, "secured": True, "active": True},
    ]


@pytest.mark.asyncio
async def test_failed_ap_read_is_skipped(monkeypatch):
    async def fake(*args):
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "call" and args[-1] == "GetDevices":
            return 0, ["/org/freedesktop/NetworkManager/Devices/2"]
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2":
            if args[4] == "DeviceType":
                return 0, 2
            if args[4] == "Interface":
                return 0, "wlan0"
            if args[4] == "State":
                return 0, 100
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[3] == "org.freedesktop.NetworkManager.Device.Wireless" and args[4] == "ActiveAccessPoint":
            return 0, "/org/freedesktop/NetworkManager/AccessPoint/10"
        if args[0] == "call" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "GetAllAccessPoints":
            return 0, ["/org/freedesktop/NetworkManager/AccessPoint/5", "/org/freedesktop/NetworkManager/AccessPoint/10"]
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/AccessPoint/5":
            if args[4] == "Ssid":
                return 1, None  # failed read - skip
            return 0, 0
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/AccessPoint/10":
            if args[4] == "Ssid":
                return 0, [ord(c) for c in "home"]
            if args[4] == "Strength":
                return 0, 40
            if args[4] == "Frequency":
                return 0, 5180
            if args[4] == "WpaFlags":
                return 0, 0
            if args[4] == "RsnFlags":
                return 0, 0
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)
    resp = await hw_routes.get_wifi()
    assert resp["access_points"] == [{"ssid": "home", "strength": 40, "frequency": 5180, "secured": False, "active": True}]


# --- Write route tests ---


@pytest.mark.asyncio
async def test_set_wifi_enabled_false_issues_set_property_call(monkeypatch):
    """(a) enabled=false issues exactly the set-property call with 'b', 'false'."""
    calls = []

    async def fake(*args):
        calls.append(args)
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "set-property" and args[1] == "org.freedesktop.NetworkManager" and args[2] == "/org/freedesktop/NetworkManager" and args[3] == "org.freedesktop.NetworkManager" and args[4] == "WirelessEnabled" and args[5] == "b" and args[6] == "false":
            return 0, None
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)
    body = hw_routes.WifiEnabledBody(enabled=False)
    resp = await hw_routes.set_wifi_enabled(body)
    assert resp == {"ok": True, "wifi_enabled": False}
    assert any(
        c[0] == "set-property" and c[5] == "b" and c[6] == "false"
        for c in calls
    ), f"Expected set-property call with 'b', 'false', got {calls}"


@pytest.mark.asyncio
async def test_nm_absent_returns_503_for_all_three_routes(monkeypatch):
    """(b) NM absent -> 503 for each of the three routes, no write call made."""
    async def fake(*args):
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 1, None
        raise AssertionError(f"unexpected busctl call (should not write): {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)

    # enabled
    resp = await hw_routes.set_wifi_enabled(hw_routes.WifiEnabledBody(enabled=True))
    assert resp.status_code == 503
    assert _json_body(resp) == {"error": "networkmanager_unavailable"}

    # disconnect
    resp = await hw_routes.wifi_disconnect(hw_routes.WifiDisconnectBody(iface="wlan0"))
    assert resp.status_code == 503
    assert _json_body(resp) == {"error": "networkmanager_unavailable"}

    # connect
    resp = await hw_routes.wifi_connect(hw_routes.WifiConnectBody(ssid="test"))
    assert resp.status_code == 503
    assert _json_body(resp) == {"error": "networkmanager_unavailable"}


@pytest.mark.asyncio
async def test_set_property_rc1_returns_502_write_failed(monkeypatch):
    """(c) set-property rc=1 -> 502 write_failed."""
    calls = []

    async def fake(*args):
        calls.append(args)
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "set-property":
            return 1, None
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)
    resp = await hw_routes.set_wifi_enabled(hw_routes.WifiEnabledBody(enabled=True))
    assert resp.status_code == 502
    assert _json_body(resp)["error"] == "write_failed"
    assert "polkit rule 50-taos-hardware.rules" in _json_body(resp)["hint"]


@pytest.mark.asyncio
async def test_disconnect_unknown_iface_returns_404(monkeypatch):
    """(d) disconnect unknown iface -> 404."""
    async def fake(*args):
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "call" and args[-1] == "GetDevices":
            return 0, ["/org/freedesktop/NetworkManager/Devices/2"]
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "DeviceType":
            return 0, 2
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "Interface":
            return 0, "wlan0"
        raise AssertionError(f"unexpected busctl call: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake)
    resp = await hw_routes.wifi_disconnect(hw_routes.WifiDisconnectBody(iface="wlan1"))
    assert resp.status_code == 404
    assert _json_body(resp) == {"error": "unknown_iface"}


@pytest.mark.asyncio
async def test_connect_with_psk_records_settings_and_no_psk_in_busctl(monkeypatch):
    """(e) connect with psk: _nm_add_and_activate gets settings with psk, wlan0 device, no permissions key, no psk in busctl calls."""
    busctl_calls = []
    nm_add_calls = []

    async def fake_busctl(*args):
        busctl_calls.append(args)
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "call" and args[-1] == "GetDevices":
            return 0, ["/org/freedesktop/NetworkManager/Devices/2"]
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "DeviceType":
            return 0, 2
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "Interface":
            return 0, "wlan0"
        raise AssertionError(f"unexpected busctl call: {args}")

    async def fake_nm_add_and_activate(settings, device_path):
        nm_add_calls.append((settings, device_path))
        return None

    monkeypatch.setattr(hw_routes, "busctl", fake_busctl)
    monkeypatch.setattr(hw_routes, "_nm_add_and_activate", fake_nm_add_and_activate)

    body = hw_routes.WifiConnectBody(ssid="MyNetwork", psk="secret123", iface="wlan0")
    resp = await hw_routes.wifi_connect(body)
    assert resp == {"ok": True}

    assert len(nm_add_calls) == 1
    settings, device_path = nm_add_calls[0]
    assert device_path == "/org/freedesktop/NetworkManager/Devices/2"
    assert "connection" in settings
    assert "802-11-wireless" in settings
    assert "802-11-wireless-security" in settings
    assert settings["802-11-wireless-security"]["key-mgmt"] == ("s", "wpa-psk")
    assert settings["802-11-wireless-security"]["psk"] == ("s", "secret123")
    assert "permissions" not in settings.get("connection", {})

    # Ensure no busctl call contains the psk string
    for call in busctl_calls:
        for arg in call:
            if isinstance(arg, str) and "secret123" in arg:
                raise AssertionError(f"PSK leaked in busctl call: {call}")


@pytest.mark.asyncio
async def test_connect_permission_denied_returns_403(monkeypatch):
    """(f) connect error name org.freedesktop.NetworkManager.PermissionDenied -> 403 not_permitted."""
    async def fake_busctl(*args):
        if args[0] == "get-property" and args[-1] == "WirelessEnabled":
            return 0, True
        if args[0] == "call" and args[-1] == "GetDevices":
            return 0, ["/org/freedesktop/NetworkManager/Devices/2"]
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "DeviceType":
            return 0, 2
        if args[0] == "get-property" and args[2] == "/org/freedesktop/NetworkManager/Devices/2" and args[4] == "Interface":
            return 0, "wlan0"
        raise AssertionError(f"unexpected busctl call: {args}")

    async def fake_nm_add_and_activate(settings, device_path):
        return "org.freedesktop.NetworkManager.PermissionDenied"

    monkeypatch.setattr(hw_routes, "busctl", fake_busctl)
    monkeypatch.setattr(hw_routes, "_nm_add_and_activate", fake_nm_add_and_activate)

    body = hw_routes.WifiConnectBody(ssid="MyNetwork", psk="secret123")
    resp = await hw_routes.wifi_connect(body)
    assert resp.status_code == 403
    assert _json_body(resp)["error"] == "not_permitted"
    assert "polkit rule 50-taos-hardware.rules" in _json_body(resp)["hint"]


@pytest.mark.asyncio
async def test_connect_psk_7_chars_returns_422_invalid_psk(monkeypatch):
    """(g) psk of 7 chars -> 422 invalid_psk and zero busctl calls."""
    busctl_calls = []

    async def fake_busctl(*args):
        busctl_calls.append(args)
        raise AssertionError(f"busctl should not be called for invalid psk: {args}")

    monkeypatch.setattr(hw_routes, "busctl", fake_busctl)
    body = hw_routes.WifiConnectBody(ssid="MyNetwork", psk="short7")  # 7 chars
    resp = await hw_routes.wifi_connect(body)
    assert resp.status_code == 422
    assert _json_body(resp) == {"error": "invalid_psk"}
    assert len(busctl_calls) == 0


@pytest.mark.asyncio
async def test_nm_add_and_activate_dbus_fast_missing(monkeypatch):
    """(h) _nm_add_and_activate returns 'dbus_fast_missing' when dbus_fast cannot be imported."""
    monkeypatch.setitem(sys.modules, "dbus_fast", None)
    monkeypatch.setitem(sys.modules, "dbus_fast.aio", None)
    monkeypatch.setitem(sys.modules, "dbus_fast.message", None)

    result = await hw_routes._nm_add_and_activate({}, "/fake/path")
    assert result == "dbus_fast_missing"


@pytest.mark.asyncio
async def test_nm_add_and_activate_bus_connect_failure_returns_dbus_call_failed(monkeypatch):
    """(i) _nm_add_and_activate returns 'dbus_call_failed' when system bus connect fails."""
    class FakeBusType:
        SYSTEM = "system"

    class FakeMessage:
        pass

    class FakeMessageType:
        ERROR = "error"

    class FakeVariant:
        def __init__(self, sig, val):
            self.sig = sig
            self.val = val

    class FakeMessageBus:
        def __init__(self, bus_type=None):
            self.bus_type = bus_type

        async def connect(self):
            raise OSError("no system bus")

    fake_dbus_fast = type("module", (), {
        "BusType": FakeBusType,
        "Message": FakeMessage,
        "MessageType": FakeMessageType,
        "Variant": FakeVariant,
    })
    fake_dbus_fast_aio = type("module", (), {
        "MessageBus": FakeMessageBus,
    })

    monkeypatch.setitem(sys.modules, "dbus_fast", fake_dbus_fast)
    monkeypatch.setitem(sys.modules, "dbus_fast.aio", fake_dbus_fast_aio)

    result = await hw_routes._nm_add_and_activate({"connection": {"id": ("s", "x")}}, "/d")
    assert result == "dbus_call_failed"
