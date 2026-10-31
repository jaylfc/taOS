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


# TestAgentBuddyDiscovery tests

AGENT_BUDDY_PROBE = b"ESP32_AGENT_COMPANION_DISCOVER_V1"


def _make_valid_reply(device_id="ABCDEF1234567890", hostname="test-device", port=8080, boot=42):
    """Create a valid Agent Buddy discovery reply JSON."""
    return {
        "service": "esp32-agent-companion",
        "protocol": 1,
        "deviceId": device_id,
        "hostname": hostname,
        "port": port,
        "boot": boot,
    }


def _make_reply_bytes(reply_dict):
    """Convert a reply dict to bytes."""
    import json
    return json.dumps(reply_dict).encode("utf-8")


def test_parse_agent_buddy_reply_valid():
    """Test parse_agent_buddy_reply with a valid reply."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply(device_id="ABCDEF1234567890", hostname="test-device", port=8080, boot=42)
    data = _make_reply_bytes(reply)

    result = parse_agent_buddy_reply(data, "192.168.1.100")

    assert result is not None
    assert result["device_id"] == "abcdef1234567890"  # lowercased
    assert result["hostname"] == "test-device"
    assert result["address"] == "192.168.1.100"
    assert result["port"] == 8080
    assert result["boot"] == 42


def test_parse_agent_buddy_reply_not_json():
    """Test parse_agent_buddy_reply returns None for non-JSON data."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    data = b"not json at all"
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_json_list():
    """Test parse_agent_buddy_reply returns None for JSON list."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    data = b"[1, 2, 3]"
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_wrong_service():
    """Test parse_agent_buddy_reply returns None for wrong service."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["service"] = "wrong-service"
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_protocol_2():
    """Test parse_agent_buddy_reply returns None for protocol 2."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["protocol"] = 2
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_protocol_true():
    """Test parse_agent_buddy_reply returns None for protocol True (bool)."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["protocol"] = True
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_device_id_15_hex():
    """Test parse_agent_buddy_reply returns None for 15-char deviceId."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply(device_id="ABCDEF123456789")  # 15 chars
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_device_id_non_hex():
    """Test parse_agent_buddy_reply returns None for non-hex deviceId."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply(device_id="ABCDEF123456789G")  # G is not hex
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_hostname_not_str():
    """Test parse_agent_buddy_reply returns None for non-str hostname."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["hostname"] = 5
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_port_0():
    """Test parse_agent_buddy_reply returns None for port 0."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply(port=0)
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_port_65536():
    """Test parse_agent_buddy_reply returns None for port 65536."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply(port=65536)
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_port_str():
    """Test parse_agent_buddy_reply returns None for port as string."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["port"] = "80"
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_port_true():
    """Test parse_agent_buddy_reply returns None for port True (bool)."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["port"] = True
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_boot_negative():
    """Test parse_agent_buddy_reply returns None for boot -1."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply(boot=-1)
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_boot_missing():
    """Test parse_agent_buddy_reply returns None for missing boot."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    del reply["boot"]
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_extra_key_ignored():
    """Test parse_agent_buddy_reply ignores extra unknown keys."""
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    reply = _make_valid_reply()
    reply["extra_unknown_key"] = "should be ignored"
    data = _make_reply_bytes(reply)
    result = parse_agent_buddy_reply(data, "192.168.1.100")

    assert result is not None
    assert result["device_id"] == "abcdef1234567890"
    assert result["hostname"] == "test-device"
    assert result["address"] == "192.168.1.100"
    assert result["port"] == 8080
    assert result["boot"] == 42
    assert "extra_unknown_key" not in result


def test_parse_agent_buddy_reply_deeply_nested_returns_none():
    """A datagram of 2000 '[' nests past the recursion limit.

    On python 3.11 json.loads raises RecursionError (3.12+ raise JSONDecodeError),
    so this test is red only on 3.11 before the fix. The monkeypatched test below
    covers the except clause on every interpreter.
    """
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    result = parse_agent_buddy_reply(b"[" * 2000, "192.168.1.100")
    assert result is None


def test_parse_agent_buddy_reply_recursion_error_returns_none(monkeypatch):
    """A RecursionError out of json.loads is a bad datagram, not a probe abort."""
    import json
    from tinyagentos.orb.discovery import parse_agent_buddy_reply

    def raise_recursion(_text):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(json, "loads", raise_recursion)
    result = parse_agent_buddy_reply(b"{}", "192.168.1.100")
    assert result is None


# Probe tests using real loopback UDP


def _start_responder(reply_bytes_list, port):
    """Start a responder thread that replies to AGENT_BUDDY_PROBE with configured datagrams."""
    import socket
    import threading

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    stop_event = threading.Event()
    received_probes = []

    def run():
        while not stop_event.is_set():
            try:
                sock.settimeout(0.1)
                data, addr = sock.recvfrom(2048)
                received_probes.append((data, addr))
                if data == AGENT_BUDDY_PROBE:
                    for reply in reply_bytes_list:
                        sock.sendto(reply, addr)
            except socket.timeout:
                continue
            except OSError:
                break

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def stop():
        stop_event.set()
        sock.close()
        thread.join(timeout=1.0)

    return stop, received_probes


def test_probe_agent_buddy_one_valid_reply():
    """Test probe_agent_buddy with one valid reply."""
    import time
    from tinyagentos.orb.discovery import probe_agent_buddy, AGENT_BUDDY_PROBE

    reply = _make_valid_reply(device_id="ABCDEF1234567890", hostname="test-device", port=8080, boot=42)
    reply_bytes = _make_reply_bytes(reply)

    # Bind to port 0 to get a free port
    import socket
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(("127.0.0.1", 0))
    responder_port = temp_sock.getsockname()[1]
    temp_sock.close()

    stop, received = _start_responder([reply_bytes], responder_port)
    try:
        start = time.monotonic()
        result = probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"], port=responder_port)
        elapsed = time.monotonic() - start
    finally:
        stop()

    assert elapsed < 1.0  # should return within timeout + 0.5s
    assert len(result) == 1
    assert result[0]["device_id"] == "abcdef1234567890"
    assert result[0]["hostname"] == "test-device"
    assert result[0]["address"] == "127.0.0.1"
    assert result[0]["port"] == 8080
    assert result[0]["boot"] == 42
    # Verify the probe was sent
    assert any(data == AGENT_BUDDY_PROBE for data, _ in received)


def test_probe_agent_buddy_two_valid_replies_sorted():
    """Test probe_agent_buddy with two valid replies, sorted by hostname."""
    import time
    from tinyagentos.orb.discovery import probe_agent_buddy, AGENT_BUDDY_PROBE

    reply1 = _make_valid_reply(device_id="AAAAAAAAAAAAAAAA", hostname="zebra-device", port=8080, boot=1)
    reply2 = _make_valid_reply(device_id="BBBBBBBBBBBBBBBB", hostname="alpha-device", port=8081, boot=2)
    # Add an invalid reply (wrong service)
    invalid_reply = _make_valid_reply(device_id="CCCCCCCCCCCCCCCC", hostname="invalid", port=8082, boot=3)
    invalid_reply["service"] = "wrong-service"
    invalid_bytes = _make_reply_bytes(invalid_reply)

    import socket
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(("127.0.0.1", 0))
    responder_port = temp_sock.getsockname()[1]
    temp_sock.close()

    stop, received = _start_responder([_make_reply_bytes(reply1), _make_reply_bytes(reply2), invalid_bytes], responder_port)
    try:
        start = time.monotonic()
        result = probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"], port=responder_port)
        elapsed = time.monotonic() - start
    finally:
        stop()

    assert elapsed < 1.0
    assert len(result) == 2
    # Should be sorted by hostname: alpha-device, then zebra-device
    assert result[0]["hostname"] == "alpha-device"
    assert result[0]["device_id"] == "bbbbbbbbbbbbbbbb"
    assert result[0]["port"] == 8081
    assert result[1]["hostname"] == "zebra-device"
    assert result[1]["device_id"] == "aaaaaaaaaaaaaaaa"
    assert result[1]["port"] == 8080
    assert any(data == AGENT_BUDDY_PROBE for data, _ in received)


def test_probe_agent_buddy_same_device_id_replaces():
    """Test probe_agent_buddy with two replies for same device_id keeps second."""
    import time
    from tinyagentos.orb.discovery import probe_agent_buddy, AGENT_BUDDY_PROBE

    # First reply with port 8080
    reply1 = _make_valid_reply(device_id="AAAAAAAAAAAAAAAA", hostname="test-device", port=8080, boot=1)
    # Second reply with same device_id but different port
    reply2 = _make_valid_reply(device_id="AAAAAAAAAAAAAAAA", hostname="test-device", port=9090, boot=2)

    import socket
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(("127.0.0.1", 0))
    responder_port = temp_sock.getsockname()[1]
    temp_sock.close()

    stop, received = _start_responder([_make_reply_bytes(reply1), _make_reply_bytes(reply2)], responder_port)
    try:
        start = time.monotonic()
        result = probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"], port=responder_port)
        elapsed = time.monotonic() - start
    finally:
        stop()

    assert elapsed < 1.0
    assert len(result) == 1
    # Should keep the second reply's port
    assert result[0]["device_id"] == "aaaaaaaaaaaaaaaa"
    assert result[0]["port"] == 9090
    assert any(data == AGENT_BUDDY_PROBE for data, _ in received)


def test_probe_agent_buddy_ignores_wrong_probe():
    """Test probe_agent_buddy responder ignores non-probe datagrams."""
    import time
    from tinyagentos.orb.discovery import probe_agent_buddy, AGENT_BUDDY_PROBE

    reply = _make_valid_reply(device_id="ABCDEF1234567890", hostname="test-device", port=8080, boot=42)

    import socket
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(("127.0.0.1", 0))
    responder_port = temp_sock.getsockname()[1]
    temp_sock.close()

    stop, received = _start_responder([_make_reply_bytes(reply)], responder_port)
    try:
        # Send a wrong probe first
        wrong_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        wrong_sock.sendto(b"WRONG_PROBE", ("127.0.0.1", responder_port))
        wrong_sock.close()

        start = time.monotonic()
        result = probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"], port=responder_port)
        elapsed = time.monotonic() - start
    finally:
        stop()

    assert elapsed < 1.0
    assert len(result) == 1
    # Responder should have received the wrong probe but only replied to the correct one
    assert any(data == b"WRONG_PROBE" for data, _ in received)
    assert any(data == AGENT_BUDDY_PROBE for data, _ in received)


def test_probe_agent_buddy_no_reply():
    """Test probe_agent_buddy with no responder reply returns empty list within timeout."""
    import time
    from tinyagentos.orb.discovery import probe_agent_buddy

    import socket
    temp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    temp_sock.bind(("127.0.0.1", 0))
    responder_port = temp_sock.getsockname()[1]
    temp_sock.close()

    # Start responder but it won't reply to anything (empty reply list)
    stop, _ = _start_responder([], responder_port)
    try:
        start = time.monotonic()
        result = probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"], port=responder_port)
        elapsed = time.monotonic() - start
    finally:
        stop()

    assert elapsed < 1.0  # timeout (0.5) + 0.5 = 1.0
    assert result == []


def test_probe_agent_buddy_socket_error_raises_transport_unavailable(monkeypatch):
    """Test probe_agent_buddy raises TransportUnavailable when socket creation fails."""
    import socket
    from tinyagentos.orb.discovery import probe_agent_buddy, TransportUnavailable

    def raise_oserror(*args, **kwargs):
        raise OSError("socket creation failed")

    monkeypatch.setattr(socket, "socket", raise_oserror)

    try:
        probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"])
    except TransportUnavailable as e:
        assert "Failed to create or bind UDP socket" in str(e)
        # Check it's chained from OSError
        assert e.__cause__ is not None
        assert isinstance(e.__cause__, OSError)
    else:
        assert False, "Expected TransportUnavailable"


def test_ipv4_broadcast_addresses_two_interfaces_lo_ipv6():
    """Test ipv4_broadcast_addresses returns per-interface broadcasts (case a)."""
    import psutil
    from types import SimpleNamespace
    from tinyagentos.orb.discovery import ipv4_broadcast_addresses

    # Mock net_if_addrs with 2 interfaces, lo, and AF_INET6
    mock_net_if_addrs = {
        "eth0": [
            SimpleNamespace(family=2, address="192.168.1.10", netmask="255.255.255.0", broadcast="192.168.1.255"),
            SimpleNamespace(family=10, address="fe80::1", netmask="ffff:ffff:ffff:ffff::", broadcast=""),
        ],
        "wlan0": [
            SimpleNamespace(family=2, address="10.0.0.5", netmask="255.0.0.0", broadcast="10.0.0.255"),
        ],
        "lo": [
            SimpleNamespace(family=2, address="127.0.0.1", netmask="255.0.0.0", broadcast=None),
        ],
    }

    # Monkeypatch psutil.net_if_addrs
    original_net_if_addrs = psutil.net_if_addrs
    psutil.net_if_addrs = lambda: mock_net_if_addrs

    try:
        result = ipv4_broadcast_addresses()
        assert result == ["10.0.0.255", "192.168.1.255"]
    finally:
        psutil.net_if_addrs = original_net_if_addrs


def test_ipv4_broadcast_addresses_duplicate_broadcasts():
    """Test ipv4_broadcast_addresses deduplicates identical broadcasts (case b)."""
    import psutil
    from types import SimpleNamespace
    from tinyagentos.orb.discovery import ipv4_broadcast_addresses

    mock_net_if_addrs = {
        "eth0": [
            SimpleNamespace(family=2, address="192.168.1.10", netmask="255.255.255.0", broadcast="192.168.1.255"),
            SimpleNamespace(family=2, address="192.168.1.11", netmask="255.255.255.0", broadcast="192.168.1.255"),
        ],
    }

    original_net_if_addrs = psutil.net_if_addrs
    psutil.net_if_addrs = lambda: mock_net_if_addrs

    try:
        result = ipv4_broadcast_addresses()
        assert result == ["192.168.1.255"]
    finally:
        psutil.net_if_addrs = original_net_if_addrs


def test_ipv4_broadcast_addresses_empty_broadcast_and_none():
    """Test ipv4_broadcast_addresses skips empty/None broadcasts (case c)."""
    import psutil
    from types import SimpleNamespace
    from tinyagentos.orb.discovery import ipv4_broadcast_addresses

    mock_net_if_addrs = {
        "eth0": [
            SimpleNamespace(family=2, address="192.168.1.10", netmask="255.255.255.0", broadcast=None),
            SimpleNamespace(family=2, address="192.168.1.11", netmask="255.255.255.0", broadcast=""),
        ],
    }

    original_net_if_addrs = psutil.net_if_addrs
    psutil.net_if_addrs = lambda: mock_net_if_addrs

    try:
        result = ipv4_broadcast_addresses()
        assert result == ["255.255.255.255"]
    finally:
        psutil.net_if_addrs = original_net_if_addrs


def test_ipv4_broadcast_addresses_empty_dict():
    """Test ipv4_broadcast_addresses with empty net_if_addrs (case d)."""
    import psutil
    from types import SimpleNamespace
    from tinyagentos.orb.discovery import ipv4_broadcast_addresses

    mock_net_if_addrs = {}

    original_net_if_addrs = psutil.net_if_addrs
    psutil.net_if_addrs = lambda: mock_net_if_addrs

    try:
        result = ipv4_broadcast_addresses()
        assert result == ["255.255.255.255"]
    finally:
        psutil.net_if_addrs = original_net_if_addrs


def test_ipv4_broadcast_addresses_skip_127_with_broadcast():
    """Test ipv4_broadcast_addresses skips 127.x addresses even with broadcast (case e)."""
    import psutil
    from types import SimpleNamespace
    from tinyagentos.orb.discovery import ipv4_broadcast_addresses

    mock_net_if_addrs = {
        "lo": [
            SimpleNamespace(family=2, address="127.0.0.1", netmask="255.0.0.0", broadcast="127.0.0.255"),
        ],
        "eth0": [
            SimpleNamespace(family=2, address="192.168.1.10", netmask="255.255.255.0", broadcast="192.168.1.255"),
        ],
    }

    original_net_if_addrs = psutil.net_if_addrs
    psutil.net_if_addrs = lambda: mock_net_if_addrs

    try:
        result = ipv4_broadcast_addresses()
        assert result == ["192.168.1.255"]
    finally:
        psutil.net_if_addrs = original_net_if_addrs


def test_probe_agent_buddy_uses_ipv4_broadcast_helper():
    """Test probe_agent_buddy uses ipv4_broadcast_addresses helper (case f)."""
    import socket
    import time
    import threading
    from tinyagentos.orb.discovery import probe_agent_buddy

    # Monkeypatch ipv4_broadcast_addresses to return a loopback address
    import tinyagentos.orb.discovery
    original_ipv4_broadcast = tinyagentos.orb.discovery.ipv4_broadcast_addresses

    def mock_ipv4_broadcast():
        return ["127.0.0.1"]

    tinyagentos.orb.discovery.ipv4_broadcast_addresses = mock_ipv4_broadcast

    # Create responder
    reply_dict = {
        "service": "esp32-agent-companion",
        "protocol": 1,
        "deviceId": "ABCDEF1234567890",
        "hostname": "test-device",
        "port": 8080,
        "boot": 42,
    }

    import json
    reply_bytes = json.dumps(reply_dict).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder_port = 0
    sock.bind(("127.0.0.1", responder_port))
    responder_port = sock.getsockname()[1]

    stop_event = threading.Event()
    received_probes = []

    def run_responder():
        while not stop_event.is_set():
            try:
                sock.settimeout(0.1)
                data, addr = sock.recvfrom(2048)
                received_probes.append((data, addr))
                if data == tinyagentos.orb.discovery.AGENT_BUDDY_PROBE:
                    sock.sendto(reply_bytes, addr)
            except socket.timeout:
                continue
            except OSError:
                break

    responder_thread = threading.Thread(target=run_responder, daemon=True)
    responder_thread.start()

    try:
        # Call probe_agent_buddy WITHOUT addresses (should use helper)
        result = probe_agent_buddy(timeout=0.5, port=responder_port)

        # Should find the device
        assert len(result) == 1
        assert result[0]["device_id"] == "abcdef1234567890"
        assert result[0]["hostname"] == "test-device"
        assert result[0]["address"] == "127.0.0.1"
        assert result[0]["port"] == 8080
        assert result[0]["boot"] == 42

        # Verify probe was sent
        assert any(data == tinyagentos.orb.discovery.AGENT_BUDDY_PROBE for data, _ in received_probes)
    finally:
        stop_event.set()
        sock.close()
        responder_thread.join(timeout=1.0)
        tinyagentos.orb.discovery.ipv4_broadcast_addresses = original_ipv4_broadcast


def test_probe_agent_buddy_explicit_addresses_bypasses_helper():
    """Test probe_agent_buddy with explicit addresses bypasses helper (case g)."""
    import socket
    import time
    import threading
    from tinyagentos.orb.discovery import probe_agent_buddy

    # Monkeypatch ipv4_broadcast_addresses to raise AssertionError
    import tinyagentos.orb.discovery
    original_ipv4_broadcast = tinyagentos.orb.discovery.ipv4_broadcast_addresses

    def mock_ipv4_broadcast():
        raise AssertionError("Helper should not be called when addresses is provided")

    tinyagentos.orb.discovery.ipv4_broadcast_addresses = mock_ipv4_broadcast

    # Create responder
    reply_dict = {
        "service": "esp32-agent-companion",
        "protocol": 1,
        "deviceId": "ABCDEF1234567890",
        "hostname": "test-device",
        "port": 8080,
        "boot": 42,
    }

    import json
    reply_bytes = json.dumps(reply_dict).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    responder_port = 0
    sock.bind(("127.0.0.1", responder_port))
    responder_port = sock.getsockname()[1]

    stop_event = threading.Event()
    received_probes = []

    def run_responder():
        while not stop_event.is_set():
            try:
                sock.settimeout(0.1)
                data, addr = sock.recvfrom(2048)
                received_probes.append((data, addr))
                if data == tinyagentos.orb.discovery.AGENT_BUDDY_PROBE:
                    sock.sendto(reply_bytes, addr)
            except socket.timeout:
                continue
            except OSError:
                break

    responder_thread = threading.Thread(target=run_responder, daemon=True)
    responder_thread.start()

    try:
        # Call probe_agent_buddy WITH explicit addresses (should bypass helper)
        result = probe_agent_buddy(timeout=0.5, addresses=["127.0.0.1"], port=responder_port)

        # Should find the device even though helper was called
        assert len(result) == 1
        assert result[0]["device_id"] == "abcdef1234567890"

        # Verify helper was NOT called (no AssertionError raised)
    finally:
        stop_event.set()
        sock.close()
        responder_thread.join(timeout=1.0)
        tinyagentos.orb.discovery.ipv4_broadcast_addresses = original_ipv4_broadcast

def test_ipv4_broadcast_addresses_net_if_addrs_oserror_falls_back(monkeypatch):
    """psutil.net_if_addrs() raising OSError degrades to the limited broadcast."""
    import psutil
    from tinyagentos.orb.discovery import ipv4_broadcast_addresses

    def _boom():
        raise OSError("interface enumeration failed")

    monkeypatch.setattr(psutil, "net_if_addrs", _boom)
    assert ipv4_broadcast_addresses() == ["255.255.255.255"]
