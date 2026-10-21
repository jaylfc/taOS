#!/usr/bin/env python3
"""Tests for tinyagentos/orb/discovery."""

import sys
import time
import unittest
from types import ModuleType, SimpleNamespace


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


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.fake_serial, self.set_ports = build_fake_serial_module()
        
        # Save original modules
        self.original_serial = sys.modules.get("serial")
        self.original_tools = sys.modules.get("serial.tools")
        self.original_list_ports = sys.modules.get("serial.tools.list_ports")
        
    def tearDown(self):
        """Clean up test fixtures."""
        # Restore original modules
        if self.original_serial:
            sys.modules["serial"] = self.original_serial
        else:
            sys.modules.pop("serial", None)
        if self.original_tools:
            sys.modules["serial.tools"] = self.original_tools
        else:
            sys.modules.pop("serial.tools", None)
        if self.original_list_ports:
            sys.modules["serial.tools.list_ports"] = self.original_list_ports
        else:
            sys.modules.pop("serial.tools.list_ports", None)
        
        # Clear discovery module cache
        sys.modules.pop("tinyagentos.orb.discovery", None)

    def test_two_espressif_ports_and_one_non_espressif(self):
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
        
        self.set_ports([espressif1, espressif2, non_espressif])
        
        # Monkey patch sys.modules
        sys.modules["serial"] = self.fake_serial
        sys.modules["serial.tools"] = self.fake_serial.tools
        sys.modules["serial.tools.list_ports"] = self.fake_serial.tools.list_ports
        
        from tinyagentos.orb.discovery import list_usb_orbs
        
        result = list_usb_orbs()
        
        # Should only have Espressif devices
        self.assertEqual(len(result), 2)
        
        # Should be sorted by port
        self.assertEqual(result[0]["port"], "/dev/ttyACM0")
        self.assertEqual(result[1]["port"], "/dev/ttyACM1")
        
        # Check dict keys and values
        self.assertEqual(result[0]["vid"], 0x303A)
        self.assertEqual(result[0]["pid"], 0x1234)
        self.assertEqual(result[0]["serial_number"], "SN123")
        self.assertEqual(result[0]["description"], "taOS Orb Companion")
        
        self.assertEqual(result[1]["vid"], 0x303A)
        self.assertEqual(result[1]["pid"], 0x5678)
        self.assertIsNone(result[1]["serial_number"])
        self.assertEqual(result[1]["description"], "taOS Orb Companion")

    def test_port_with_vid_none_is_skipped(self):
        """Test that ports with None VID are skipped."""
        # Set up test port with None VID
        port_with_none_vid = SimpleNamespace(
            device="/dev/ttyACM3",
            vid=None,  # None VID should be skipped
            pid=0x1234,
            serial_number="SN789",
            description="Unknown Device"
        )
        
        self.set_ports([port_with_none_vid])
        
        # Monkey patch sys.modules
        sys.modules["serial"] = self.fake_serial
        sys.modules["serial.tools"] = self.fake_serial.tools
        sys.modules["serial.tools.list_ports"] = self.fake_serial.tools.list_ports
        
        from tinyagentos.orb.discovery import list_usb_orbs
        
        result = list_usb_orbs()
        
        # Should be empty since VID is None
        self.assertEqual(result, [])

    def test_no_ports_returns_empty_list(self):
        """Test that no ports returns empty list, not an error."""
        self.set_ports([])
        
        # Monkey patch sys.modules
        sys.modules["serial"] = self.fake_serial
        sys.modules["serial.tools"] = self.fake_serial.tools
        sys.modules["serial.tools.list_ports"] = self.fake_serial.tools.list_ports
        
        from tinyagentos.orb.discovery import list_usb_orbs
        
        result = list_usb_orbs()
        
        # Should be empty list
        self.assertEqual(result, [])

    def test_pyserial_missing_raises_transport_unavailable(self):
        """Test that missing pyserial raises TransportUnavailable."""
        # Replace sys.modules['serial'] with None to simulate missing pyserial
        # We don't need to import serial to get the original module here
        original_serial = sys.modules.get("serial")
        sys.modules["serial"] = None
        
        try:
            # Clear import cache for discovery module
            sys.modules.pop("tinyagentos.orb.discovery", None)
            
            from tinyagentos.orb.discovery import list_usb_orbs, TransportUnavailable
            
            with self.assertRaises(TransportUnavailable) as cm:
                list_usb_orbs()
            
            self.assertIn("pyserial not installed", str(cm.exception))
            self.assertIn("tinyagentos[orb]", str(cm.exception))
            
            # Check that TransportUnavailable is a subclass of OrbError
            from tinyagentos.orb.frames import OrbError
            self.assertTrue(issubclass(TransportUnavailable, OrbError))
            
        finally:
            # Restore original modules
            if original_serial:
                sys.modules["serial"] = original_serial
            else:
                del sys.modules["serial"]
            
            # Clear discovery module cache
            sys.modules.pop("tinyagentos.orb.discovery", None)


def build_fake_zeroconf_module():
    """Build a fake zeroconf module with mock Zeroconf and ServiceBrowser classes."""
    fake_zeroconf = ModuleType("zeroconf")
    
    # This will be set by tests
    fake_zeroconf._service_infos = {}
    fake_zeroconf._collected_names = []
    fake_zeroconf._close_called = False
    fake_zeroconf._raise_on_get_info = None
    
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
            # Immediately call add_service for each preset name
            for name in fake_zeroconf._collected_names:
                listener.add_service(zc, type_, name)
    
    fake_zeroconf.Zeroconf = FakeZeroconf
    fake_zeroconf.ServiceBrowser = FakeServiceBrowser
    
    return fake_zeroconf


class TestLANDiscovery(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.fake_zeroconf = build_fake_zeroconf_module()
        
        # Save original modules
        self.original_zeroconf = sys.modules.get("zeroconf")
        self.original_time_sleep = time.sleep
        
        # Monkey patch
        sys.modules["zeroconf"] = self.fake_zeroconf
        time.sleep = lambda x: None  # No-op sleep
        
        # Clear discovery module cache
        sys.modules.pop("tinyagentos.orb.discovery", None)
    
    def tearDown(self):
        """Clean up test fixtures."""
        # Restore original modules
        if self.original_zeroconf:
            sys.modules["zeroconf"] = self.original_zeroconf
        else:
            sys.modules.pop("zeroconf", None)
        time.sleep = self.original_time_sleep
        
        # Clear discovery module cache
        sys.modules.pop("tinyagentos.orb.discovery", None)
    
    def _make_service_info(self, name, port=80, properties=None, addresses=None):
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
            parsed_addresses=lambda: sorted(addresses)
        )
    
    def test_two_orbs_out_of_order_sorted_by_name(self):
        """Test two Orbs announced out of order are sorted by name."""
        # Set up service infos
        self.fake_zeroconf._service_infos = {
            "orb-living._taos-orb._tcp.local.": self._make_service_info(
                "orb-living._taos-orb._tcp.local.",
                port=80,
                properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.2.3"},
                addresses=["192.168.1.101", "10.0.0.1"]
            ),
            "orb-kitchen._taos-orb._tcp.local.": self._make_service_info(
                "orb-kitchen._taos-orb._tcp.local.",
                port=8080,
                properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
                addresses=["192.168.1.100"]
            ),
        }
        self.fake_zeroconf._collected_names = [
            "orb-living._taos-orb._tcp.local.",
            "orb-kitchen._taos-orb._tcp.local.",
        ]
        
        from tinyagentos.orb.discovery import browse_lan_orbs
        
        result = browse_lan_orbs(timeout=2.0)
        
        # Should have 2 results sorted by name
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["name"], "orb-kitchen")
        self.assertEqual(result[1]["name"], "orb-living")
        
        # Check orb-kitchen
        self.assertEqual(result[0]["addresses"], ["192.168.1.100"])
        self.assertEqual(result[0]["port"], 8080)
        self.assertEqual(result[0]["board"], "esp32s3")
        self.assertEqual(result[0]["app"], "orb-companion")
        self.assertEqual(result[0]["version"], "1.0.0")
        
        # Check orb-living
        self.assertEqual(result[1]["addresses"], ["10.0.0.1", "192.168.1.101"])
        self.assertEqual(result[1]["port"], 80)
        self.assertEqual(result[1]["board"], "esp32s3")
        self.assertEqual(result[1]["app"], "orb-companion")
        self.assertEqual(result[1]["version"], "1.2.3")
        
        # Verify close was called
        self.assertTrue(self.fake_zeroconf._close_called)
    
    def test_duplicate_name_deduplicated(self):
        """Test same name announced twice results in one entry."""
        self.fake_zeroconf._service_infos = {
            "orb-kitchen._taos-orb._tcp.local.": self._make_service_info(
                "orb-kitchen._taos-orb._tcp.local.",
                port=80,
                properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
            ),
        }
        self.fake_zeroconf._collected_names = [
            "orb-kitchen._taos-orb._tcp.local.",
            "orb-kitchen._taos-orb._tcp.local.",  # duplicate
        ]
        
        from tinyagentos.orb.discovery import browse_lan_orbs
        
        result = browse_lan_orbs(timeout=2.0)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "orb-kitchen")
        self.assertTrue(self.fake_zeroconf._close_called)
    
    def test_get_service_info_returns_none_skipped(self):
        """Test get_service_info returning None for a name skips it."""
        self.fake_zeroconf._service_infos = {
            "orb-kitchen._taos-orb._tcp.local.": self._make_service_info(
                "orb-kitchen._taos-orb._tcp.local.",
                port=80,
                properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
            ),
            # orb-living has no service info (None)
        }
        self.fake_zeroconf._collected_names = [
            "orb-kitchen._taos-orb._tcp.local.",
            "orb-living._taos-orb._tcp.local.",
        ]
        
        from tinyagentos.orb.discovery import browse_lan_orbs
        
        result = browse_lan_orbs(timeout=2.0)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "orb-kitchen")
        self.assertTrue(self.fake_zeroconf._close_called)
    
    def test_missing_ver_key_version_none(self):
        """Test missing 'ver' key in TXT gives version None."""
        self.fake_zeroconf._service_infos = {
            "orb-kitchen._taos-orb._tcp.local.": self._make_service_info(
                "orb-kitchen._taos-orb._tcp.local.",
                port=80,
                properties={"board": "esp32s3", "app": "orb-companion"},  # no 'ver'
            ),
        }
        self.fake_zeroconf._collected_names = [
            "orb-kitchen._taos-orb._tcp.local.",
        ]
        
        from tinyagentos.orb.discovery import browse_lan_orbs
        
        result = browse_lan_orbs(timeout=2.0)
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "orb-kitchen")
        self.assertIsNone(result[0]["version"])
        self.assertEqual(result[0]["board"], "esp32s3")
        self.assertEqual(result[0]["app"], "orb-companion")
        self.assertTrue(self.fake_zeroconf._close_called)
    
    def test_no_names_returns_empty_list_close_called(self):
        """Test no announced names returns empty list and close is still called."""
        self.fake_zeroconf._service_infos = {}
        self.fake_zeroconf._collected_names = []
        
        from tinyagentos.orb.discovery import browse_lan_orbs
        
        result = browse_lan_orbs(timeout=2.0)
        
        self.assertEqual(result, [])
        self.assertTrue(self.fake_zeroconf._close_called)
    
    def test_get_service_info_raises_error_propagates_close_called(self):
        """Test RuntimeError from get_service_info propagates and close is called."""
        self.fake_zeroconf._service_infos = {
            "orb-kitchen._taos-orb._tcp.local.": self._make_service_info(
                "orb-kitchen._taos-orb._tcp.local.",
                port=80,
                properties={"board": "esp32s3", "app": "orb-companion", "ver": "1.0.0"},
            ),
        }
        self.fake_zeroconf._collected_names = [
            "orb-kitchen._taos-orb._tcp.local.",
        ]
        self.fake_zeroconf._raise_on_get_info = RuntimeError("mDNS error")
        
        from tinyagentos.orb.discovery import browse_lan_orbs
        
        with self.assertRaises(RuntimeError) as cm:
            browse_lan_orbs(timeout=2.0)
        
        self.assertEqual(str(cm.exception), "mDNS error")
        self.assertTrue(self.fake_zeroconf._close_called)


if __name__ == "__main__":
    unittest.main()