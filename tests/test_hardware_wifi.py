"""Hardware Wi-Fi read route via NetworkManager D-Bus (busctl)."""

from __future__ import annotations

import pytest

from tinyagentos.routes import hardware_wifi as hw_routes


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
