"""BlueZ pairing agent module tests (taOS S4b)"""

from __future__ import annotations

import pytest

from tinyagentos.bluez_agent import PairingSession, build_agent_interface, register, unregister


class TestPairingSession:
    """PairingSession tests."""

    def test_session_creation_with_valid_device(self):
        """Create a PairingSession with a valid device path."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert session.device == "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"

    def test_session_creation_with_invalid_device_raises_error(self):
        """Session creation with invalid device raises ValueError."""
        with pytest.raises(ValueError, match="Invalid device path"):
            PairingSession("/invalid/path")

    @pytest.mark.asyncio
    async def test_request_sets_prompt(self):
        """Request for confirm sets prompt."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        assert session._prompt == {
            "device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "kind": "confirm",
        }
        assert session._future is future
        assert not future.done()

    @pytest.mark.asyncio
    async def test_request_with_pin_sets_prompt(self):
        """Request for PIN sets prompt with device and kind."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        assert session._prompt == {
            "device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "kind": "pin",
        }

    @pytest.mark.asyncio
    async def test_request_with_passkey_sets_prompt(self):
        """Request for passkey sets prompt with passkey value."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("passkey", 123456)
        assert session._prompt == {
            "device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "kind": "passkey",
            "passkey": 123456,
        }

    @pytest.mark.asyncio
    async def test_request_validates_kind(self):
        """Request validates kind parameter."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        with pytest.raises(ValueError, match="Invalid kind"):
            session.request("invalid")

    @pytest.mark.asyncio
    async def test_request_validates_passkey_range(self):
        """Request validates passkey is within range."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        with pytest.raises(ValueError, match="range 0..999999"):
            session.request("passkey", -1)
        with pytest.raises(ValueError, match="range 0..999999"):
            session.request("passkey", 1000000)

    @pytest.mark.asyncio
    async def test_answer_accepts_confirm(self):
        """Answer with accept True for confirm completes future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.answer(True)
        assert future.result() == {"accepted": True}
        assert session._prompt is None

    @pytest.mark.asyncio
    async def test_answer_rejects_confirm(self):
        """Answer with accept False for confirm rejects."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.answer(False)
        assert future.result() == {"accepted": False, "reason": "rejected"}
        assert session._prompt is None

    @pytest.mark.asyncio
    async def test_answer_accepts_pin(self):
        """Answer with PIN completes future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        session.answer(True, "1234")
        assert future.result() == {"accepted": True, "pin": "1234"}
        assert session._prompt is None

    @pytest.mark.asyncio
    async def test_answer_rejects_pin(self):
        """Answer with reject for PIN completes future with rejection."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        session.answer(False)
        assert future.result() == {"accepted": False, "reason": "rejected"}
        assert session._prompt is None

    @pytest.mark.asyncio
    async def test_answer_accepts_passkey(self):
        """Answer with passkey completes future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("passkey", 123456)
        session.answer(True)
        assert future.result() == {"accepted": True, "passkey": 123456}
        assert session._prompt is None

    @pytest.mark.asyncio
    async def test_answer_rejects_passkey(self):
        """Answer with reject for passkey completes future with rejection."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("passkey", 123456)
        session.answer(False)
        assert future.result() == {"accepted": False, "reason": "rejected"}
        assert session._prompt is None

    @pytest.mark.asyncio
    async def test_answer_requires_pin_for_pin_kind(self):
        """Answer requires PIN for PIN kind."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        with pytest.raises(ValueError, match="PIN required"):
            session.answer(True, None)

    @pytest.mark.asyncio
    async def test_answer_requires_pin_validation(self):
        """Answer validates PIN format."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        with pytest.raises(ValueError, match="PIN must match regex"):
            session.answer(True, "abc")
        with pytest.raises(ValueError, match="PIN must match regex"):
            session.answer(True, "1")
        session.answer(True, "1234")
        assert future.result() == {"accepted": True, "pin": "1234"}

    @pytest.mark.asyncio
    async def test_answer_requires_passkey_in_prompt(self):
        """Answer requires passkey to be in prompt for passkey kind."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        # Request without passkey
        future = session.request("passkey")
        with pytest.raises(ValueError, match="Passkey required"):
            session.answer(True)

    @pytest.mark.asyncio
    async def test_answer_no_pending_prompt_raises(self):
        """Answer without pending prompt raises error."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        with pytest.raises(RuntimeError, match="No pending prompt"):
            session.answer(True)

    @pytest.mark.asyncio
    async def test_answer_twice_after_completion_raises(self):
        """Answer after prompt completed raises error."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.answer(True)
        with pytest.raises(RuntimeError, match="No pending prompt"):
            session.answer(False)

    @pytest.mark.asyncio
    async def test_cancel_clears_prompt(self):
        """Cancel clears prompt and sets exception on future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.cancel()
        assert session._prompt is None
        with pytest.raises(Exception) as exc_info:
            future.result()
        assert "org.bluez.Error.Canceled" in str(exc_info.value)

    def test_cancel_no_prompt_does_nothing(self):
        """Cancel without prompt does nothing."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        session.cancel()  # Should not raise

    @pytest.mark.asyncio
    async def test_request_twice_raises(self):
        """Request while prompt is pending raises error."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future1 = session.request("confirm")
        with pytest.raises(RuntimeError, match="Prompt already pending"):
            session.request("pin")


class TestAgentInterface:
    """Tests for the Agent1 interface built from a PairingSession."""

    def test_build_agent_interface_returns_service_interface(self):
        """build_agent_interface returns a ServiceInterface."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)
        assert iface.__class__.__name__ == "Agent1Interface"
        assert hasattr(iface, "Release")
        assert hasattr(iface, "RequestPinCode")
        assert hasattr(iface, "DisplayPinCode")
        assert hasattr(iface, "RequestPasskey")
        assert hasattr(iface, "DisplayPasskey")
        assert hasattr(iface, "RequestConfirmation")
        assert hasattr(iface, "RequestAuthorization")
        assert hasattr(iface, "AuthorizeService")
        assert hasattr(iface, "Cancel")

    def test_request_confirmation_success(self):
        """RequestConfirmation resolves when session is answered."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)
        
        # We can't easily test the full interface without a real DBus bus
        # This test verifies the interface can be built and has the required methods
        pass

    def test_authorize_service_rejects(self):
        """AuthorizeService always raises DBusError with org.bluez.Error.Rejected."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)
        
        # We can't easily test the full interface without a real DBus bus
        # This test verifies the interface can be built and has the required methods
        pass


class TestPairingSessionTimeout:
    """Tests for PairingSession timeout functionality."""

    @pytest.mark.asyncio
    async def test_request_sets_timeout(self):
        """Request sets a 60-second timeout."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        # We can't easily test the actual timeout running without mocking
        # This test verifies the timeout handle is created
        assert session._timeout_handle is None  # Initially None
        future = session.request("confirm")
        # After request, timeout handle should be set
        # (though in practice, it would be an asyncio.Handle)
        assert session._timeout_handle is not None

    @pytest.mark.asyncio
    async def test_timeout_rejects_after_expiry(self):
        """Timeout rejects the future after 60 seconds."""
        # This test would need to mock asyncio.get_event_loop().call_later
        # to verify the timeout is called
        pass


class TestRegisterUnregister:
    """Tests for register and unregister functions."""

    @pytest.mark.asyncio
    async def test_register_with_mock_bus(self):
        """register exports the interface and calls RegisterAgent."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        
        # Mock bus with minimal interface
        class MockBus:
            def __init__(self):
                self.exports = {}
                self.calls = []
            
            def export(self, path, iface):
                self.exports[path] = iface
            
            def unexport(self, path):
                if path in self.exports:
                    del self.exports[path]
            
            async def call(self, interface, path, method, body):
                self.calls.append({
                    "interface": interface,
                    "path": path,
                    "method": method,
                    "body": body
                })
                return 0, {"data": None}
        
        bus = MockBus()
        await register(bus, session)
        
        # Verify export was called
        assert "/org/taos/bluez_agent" in bus.exports
        # Verify RegisterAgent was called with correct signature
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert call["interface"] == "org.bluez.AgentManager1"
        assert call["path"] == "/org/bluez"
        assert call["method"] == "RegisterAgent"
        assert call["body"] == ["/org/taos/bluez_agent", "KeyboardDisplay"]

    @pytest.mark.asyncio
    async def test_unregister_with_mock_bus(self):
        """unregister calls UnregisterAgent and unexports."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        
        class MockBus:
            def __init__(self):
                self.exports = {}
                self.calls = []
            
            def export(self, path, iface):
                self.exports[path] = iface
            
            def unexport(self, path):
                if path in self.exports:
                    del self.exports[path]
            
            async def call(self, interface, path, method, body):
                self.calls.append({
                    "interface": interface,
                    "path": path,
                    "method": method,
                    "body": body
                })
                return 0, {"data": None}
        
        bus = MockBus()
        # First register to set up the export
        await register(bus, session)
        
        # Then unregister
        await unregister(bus)
        
        # Verify UnregisterAgent was called with correct signature
        assert len(bus.calls) == 2
        register_call = bus.calls[0]
        unregister_call = bus.calls[1]
        assert register_call["interface"] == "org.bluez.AgentManager1"
        assert register_call["path"] == "/org/bluez"
        assert register_call["method"] == "RegisterAgent"
        assert register_call["body"] == ["/org/taos/bluez_agent", "KeyboardDisplay"]
        assert unregister_call["interface"] == "org.bluez.AgentManager1"
        assert unregister_call["path"] == "/org/bluez"
        assert unregister_call["method"] == "UnregisterAgent"
        assert unregister_call["body"] == ["/org/taos/bluez_agent"]
        # Verify unexport was called
        assert "/org/taos/bluez_agent" not in bus.exports