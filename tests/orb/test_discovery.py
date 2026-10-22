#!/usr/bin/env python3
"""Tests for tinyagentos/orb/discovery."""

import sys
import time
from types import ModuleType, SimpleNamespace

import pytest


def build_fake_serial_module():
    """Build a fake serial module with mock ports."""
    # Create the module tree
    fake_serial = ModuleType("serial")
    fake_serial.tools = ModuleType("serial.tools")

    # Create list_ports module
    fake_list_ports = ModuleType("serial.tools.list_ports")

    # Store test data as a closure variable
    test_ports = []

    def mock_comports():
        return test_ports

    fake_list_ports.comports = mock_comports
    fake_serial.tools.list_ports = fake_list_ports

    # Helper function to set test ports
    def set_ports(ports):
        nonlocal test_ports
        test_ports = ports

    return fake_serial, set_ports


def build_fake_zeroconf_module():
    """Build a fake zeroconf module with mock Zeroconf and ServiceBrowser classes."""
    fake_zeroconf = ModuleType("zeroconf")

    # This will be set by tests
    fake_zeroconf._service_infos = {}
    fake_zeroconf._collected_names = []
    fake_zeroconf._close_called = False
    fake_zeroconf._raise_on_get_info = None
    fake_zeroconf._raise_on_browser_init = None

    class FakeZeroconf:
        def __init__(self):
            pass

        def get_service_info(self, type_, name, timeout=1000):
            if fake_zeroconf._raise_on_get_info:
                raise fake_zeroconf._raise_on_get_info
            return fake_zeroconf._service_infos.get(name)

        def close(self):
            fake_zeroconf._close_called = True

    class FakeServiceBrowser:
        def __init__(self, zc, type_, listener):
            if fake_zeroconf._raise_on_browser_init:
                raise fake_zeroconf._raise_on_browser_init
            # Immediately call add_service for each preset name
            for name in fake_zeroconf._collected_names:
                listener.add_service(zc, type_, name)

    fake_zeroconf.Zeroconf = FakeZeroconf
    fake_zeroconf.ServiceBrowser = FakeServiceBrowser

    return fake_zeroconf


@pytest.fixture
def fake_serial(monkeypatch):
    """Install a fake serial module tree and return the set_ports helper."""
    fake_serial, set_ports = build_fake_serial_module()
    monkeypatch.setitem(sys.modules, "serial", fake_serial)
    monkeypatch.setitem(sys.modules, "serial.tools", fake_serial.tools)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", fake_serial.tools.list_ports)
    return set_ports


@pytest.fixture
def fake_zeroconf(monkeypatch):
    """Install a fake zeroconf module, make time.sleep a no-op, and return the fake."""
    fake_zeroconf = build_fake_zeroconf_module()
    monkeypatch.setitem(sys.modules, "zeroconf", fake_zeroconf)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return fake_zeroconf


def _make_service_info(name, port=80, properties=None, addresses=None):
    """Create a fake ServiceInfo-like object."""
    if properties is None:
        properties = {}
    if addresses is None:
        addresses = ["192.168.1.100"]

    # Convert properties to bytes keys and values
    byte_props = {}
    for k, v in properties.items():
        byte_props[k.encode("utf-8")] = v.encode("utf-8") if v is not None else None

    return SimpleNamespace(
        name=name,
        port=port,
        properties=byte_props,
        parsed_addresses=lambda: list(addresses)
    )


# TestDiscovery tests (formerly TestDiscovery class)

def test_two_espressif_ports_and_one_non_espressif(fake_serial):
    """Test discovery finds exactly Espressif devices, sorted by port."""
    # Set up test ports
    espressif1 = SimpleNamespace(
        device="/dev/ttyACM0",
        vid=0x303A,
        pid=0x1234,
        serial_number="SN123",
        description="taOS Orb Companion"
    )
    espressif2 = SimpleNamespace(
        device="/dev/ttyACM1",
        vid=0x303A,
        pid=0x5678,
        serial_number=None,
        description="taOS Orb Companion"
    )
    non_espressif = SimpleNamespace(
        device="/dev/ttyACM2",
        vid=0x2341,  # Arduino VID
        pid=0x0043,
        serial_number="SN456",
        description="Arduino"
    )

    fake_serial([espressif1, espressif2, non_espressif])

    from tinyagentos.orb.discovery import list_usb_orbs

    result = list_usb_orbs()

    # Should only have Espressif devices
    assert len(result) == 2

    # Should be sorted by port
    assert result[0]["port"] == "/dev/ttyACM0"
    assert result[1]["port"] == "/dev/ttyACM1"

    # Check dict keys and values
    assert result[0]["vid"] == 0x303A
    assert result[0]["pid"] == 0x1234
    assert result[0]["serial_number"] == "SN123"
    assert result[0]["description"] == "taOS Orb Companion"

    assert result[1]["vid"] == 0x303A
    assert result[1]["pid"] == 0x5678
    assert result[1]["serial_number"] is None
    assert result[1]["description"] == "taOS Orb Companion"


def test_port_with_vid_none_is_skipped(fake_serial):
    """Test that ports with None VID are skipped."""
    # Set up test port with None VID
    port_with_none_vid = SimpleNamespace(
        device="/dev/ttyACM3",
        vid=None,  # None VID should be skipped
        pid=0x1234,
        serial_number="SN789",
        description="Unknown Device"
    )

    fake_serial([port_with_none_vid])

    from tinyagentos.orb.discovery import list_usb_orbs

    result = list_usb_orbs()

    # Should be empty since VID is None
    assert result == []


def test_no_ports_returns_empty_list(fake_serial):
    """Test that no ports returns empty list, not an error."""
    fake_serial([])

    from tinyagentos.orb.discovery import list_usb_orbs

    result = list_usb_orbs()

    # Should be empty list
    assert result == []


def test_pyserial_missing_raises_transport_unavailable(monkeypatch):
    """Test that missing pyserial raises TransportUnavailable."""
    monkeypatch.setitem(sys.modules, "serial", None)
    monkeypatch.setitem(sys.modules, "serial.tools", None)
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", None)

    from tinyagentos.orb.discovery import list_usb_orbs, TransportUnavailable

    try:
        list_usb_orbs()
    except TransportUnavailable as e:
        assert "pyserial not installed" in str(e)
        assert "tinyagentos[orb]" in str(e)

        # Check that TransportUnavailable is a subclass of OrbError
        from tinyagentos.orb.frames import OrbError
        assert issubclass(TransportUnavailable, OrbError)
    else:
        assert False, "Expected TransportUnavailable"


# TestLANDiscovery tests (formerly TestLANDiscovery class)

def test_two_orbs_out_of_order_sorted_by_name(fake_zeroconf):
    """Test two Orbs announced out of order are sorted by name."""
    # Set up service infos
    # Addresses for orb-living are NOT in lexicographic order ("3" > "20" as strings)
    # discovery.py's sorted() will sort them lexicographically
    fake_zeroconf._service_infos = {
        "orb-living._taos-orb._tcp.local.": _make_service_info(
            "orb-living._taos-orb._tcp.local.",
            port=80,
            properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.2.3"},
            addresses=["192.168.1.3", "192.168.1.20"]
        ),
        "orb-kitchen._taos-orb._tcp.local.": _make_service_info(
            "orb-kitchen._taos-orb._tcp.local.",
            port=8080,
            properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
            addresses=["192.168.1.100"]
        ),
    }
    fake_zeroconf._collected_names = [
        "orb-living._taos-orb._tcp.local.",
        "orb-kitchen._taos-orb._tcp.local.",
    ]

    from tinyagentos.orb.discovery import browse_lan_orbs

    result = browse_lan_orbs(timeout=2.0)

    # Should have 2 results sorted by name
    assert len(result) == 2
    assert result[0]["name"] == "orb-kitchen"
    assert result[1]["name"] == "orb-living"

    # Check orb-kitchen
    assert result[0]["addresses"] == ["192.168.1.100"]
    assert result[0]["port"] == 8080
    assert result[0]["board"] == "esp32s3"
    assert result[0]["app"] == "orb-companion"
    assert result[0]["version"] == "1.0.0"

    # Check orb-living - addresses should be sorted lexicographically by discovery.py
    assert result[1]["addresses"] == ["192.168.1.20", "192.168.1.3"]
    assert result[1]["port"] == 80
    assert result[1]["board"] == "esp32s3"
    assert result[1]["app"] == "orb-companion"
    assert result[1]["version"] == "1.2.3"

    # Verify close was called
    assert fake_zeroconf._close_called


def test_duplicate_name_deduplicated(fake_zeroconf):
    """Test same name announced twice results in one entry."""
    fake_zeroconf._service_infos = {
        "orb-kitchen._taos-orb._tcp.local.": _make_service_info(
            "orb-kitchen._taos-orb._tcp.local.",
            port=80,
            properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
        ),
    }
    fake_zeroconf._collected_names = [
        "orb-kitchen._taos-orb._tcp.local.",
        "orb-kitchen._taos-orb._tcp.local.",  # duplicate
    ]

    from tinyagentos.orb.discovery import browse_lan_orbs

    result = browse_lan_orbs(timeout=2.0)

    assert len(result) == 1
    assert result[0]["name"] == "orb-kitchen"
    assert fake_zeroconf._close_called


def test_get_service_info_returns_none_skipped(fake_zeroconf):
    """Test get_service_info returning None for a name skips it."""
    fake_zeroconf._service_infos = {
        "orb-kitchen._taos-orb._tcp.local.": _make_service_info(
            "orb-kitchen._taos-orb._tcp.local.",
            port=80,
            properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
        ),
        # orb-living has no service info (None)
    }
    fake_zeroconf._collected_names = [
        "orb-kitchen._taos-orb._tcp.local.",
        "orb-living._taos-orb._tcp.local.",
    ]

    from tinyagentos.orb.discovery import browse_lan_orbs

    result = browse_lan_orbs(timeout=2.0)

    assert len(result) == 1
    assert result[0]["name"] == "orb-kitchen"
    assert fake_zeroconf._close_called


def test_missing_ver_key_version_none(fake_zeroconf):
    """Test missing 'ver' key in TXT gives version None."""
    fake_zeroconf._service_infos = {
        "orb-kitchen._taos-orb._tcp.local.": _make_service_info(
            "orb-kitchen._taos-orb._tcp.local.",
            port=80,
            properties={"board": "esp32s3", "app": "orb-companion"},  # no 'ver'
        ),
    }
    fake_zeroconf._collected_names = [
        "orb-kitchen._taos-orb._tcp.local.",
    ]

    from tinyagentos.orb.discovery import browse_lan_orbs

    result = browse_lan_orbs(timeout=2.0)

    assert len(result) == 1
    assert result[0]["name"] == "orb-kitchen"
    assert result[0]["version"] is None
    assert result[0]["board"] == "esp32s3"
    assert result[0]["app"] == "orb-companion"
    assert fake_zeroconf._close_called


def test_no_names_returns_empty_list_close_called(fake_zeroconf):
    """Test no announced names returns empty list and close is still called."""
    fake_zeroconf._service_infos = {}
    fake_zeroconf._collected_names = []

    from tinyagentos.orb.discovery import browse_lan_orbs

    result = browse_lan_orbs(timeout=2.0)

    assert result == []
    assert fake_zeroconf._close_called


def test_get_service_info_raises_error_propagates_close_called(fake_zeroconf):
    """Test RuntimeError from get_service_info propagates and close is called."""
    fake_zeroconf._service_infos = {
        "orb-kitchen._taos-orb._tcp.local.": _make_service_info(
            "orb-kitchen._taos-orb._tcp.local.",
            port=80,
            properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
        ),
    }
    fake_zeroconf._collected_names = [
        "orb-kitchen._taos-orb._tcp.local.",
    ]
    fake_zeroconf._raise_on_get_info = RuntimeError("mDNS error")

    from tinyagentos.orb.discovery import browse_lan_orbs

    try:
        browse_lan_orbs(timeout=2.0)
    except RuntimeError as e:
        assert str(e) == "mDNS error"
        assert fake_zeroconf._close_called
    else:
        assert False, "Expected RuntimeError"


def test_service_browser_init_raises_error_propagates_close_called(fake_zeroconf):
    """Test RuntimeError from ServiceBrowser __init__ propagates and close is called."""
    fake_zeroconf._raise_on_browser_init = RuntimeError("browser init failed")

    from tinyagentos.orb.discovery import browse_lan_orbs

    try:
        browse_lan_orbs(timeout=2.0)
    except RuntimeError as e:
        assert str(e) == "browser init failed"
        assert fake_zeroconf._close_called
    else:
        assert False, "Expected RuntimeError"