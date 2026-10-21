#!/usr/bin/env python3
"""Tests for tinyagentos/orb/discovery."""

import sys
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


if __name__ == "__main__":
    unittest.main()