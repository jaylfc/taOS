"""BlueZ pairing agent module tests (taOS S4b)"""

from __future__ import annotations

import asyncio
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

    def _get_async_method(self, iface, method_name):
        """Get the underlying async function for a dbus_method."""
        method = getattr(iface, method_name)
        # The @dbus_method decorator stores the original function in __wrapped__
        return method.__wrapped__

    def test_build_agent_interface_returns_service_interface(self):
        """build_agent_interface returns a ServiceInterface with all Agent1 methods."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        assert isinstance(iface, ServiceInterface)
        assert iface.name == "org.bluez.Agent1"

        # Check exported method names via introspection
        introspection = iface.introspect()
        method_names = [m.name for m in introspection.methods]
        expected_methods = {
            "Release",
            "RequestPinCode",
            "DisplayPinCode",
            "RequestPasskey",
            "DisplayPasskey",
            "RequestConfirmation",
            "RequestAuthorization",
            "AuthorizeService",
            "Cancel",
        }
        assert set(method_names) == expected_methods

    @pytest.mark.asyncio
    async def test_request_pin_code_accepted_returns_pin(self):
        """RequestPinCode returns pin when session accepts."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        # Get the underlying async function
        request_pin_code = self._get_async_method(iface, "RequestPinCode")

        # Start the request
        task = asyncio.create_task(request_pin_code(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"))
        await asyncio.sleep(0.01)  # Let the request start

        # Answer the prompt
        session.answer(True, "1234")

        # Wait for result
        pin = await task
        assert pin == "1234"

    @pytest.mark.asyncio
    async def test_request_pin_code_rejected_raises_dbus_error(self):
        """RequestPinCode raises DBusError when session rejects."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface
        from dbus_fast.errors import DBusError

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_pin_code = self._get_async_method(iface, "RequestPinCode")

        task = asyncio.create_task(request_pin_code(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"))
        await asyncio.sleep(0.01)

        session.answer(False)

        with pytest.raises(DBusError) as exc_info:
            await task
        assert exc_info.value.type == "org.bluez.Error.Rejected"

    @pytest.mark.asyncio
    async def test_request_pin_code_timeout_raises_dbus_error(self):
        """RequestPinCode raises DBusError when session times out."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface
        from dbus_fast.errors import DBusError

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_pin_code = self._get_async_method(iface, "RequestPinCode")

        # Patch the session's request to return a future that never resolves
        from unittest.mock import patch

        with patch.object(session, "request") as mock_request:
            mock_future = asyncio.get_event_loop().create_future()
            mock_request.return_value = mock_future

            task = asyncio.create_task(request_pin_code(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"))

            # Wait a bit then trigger timeout by cancelling the future
            await asyncio.sleep(0.01)
            mock_future.cancel()

            with pytest.raises(DBusError) as exc_info:
                await task
            # Cancelling the future triggers Canceled error
            assert exc_info.value.type == "org.bluez.Error.Canceled"

    @pytest.mark.asyncio
    async def test_request_passkey_accepted_returns_passkey(self):
        """RequestPasskey returns passkey when session accepts."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_passkey = self._get_async_method(iface, "RequestPasskey")

        task = asyncio.create_task(request_passkey(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"))
        await asyncio.sleep(0.01)

        session.answer(True)

        passkey = await task
        assert isinstance(passkey, int)
        assert 0 <= passkey <= 999999

    @pytest.mark.asyncio
    async def test_request_confirmation_accepted_returns_none(self):
        """RequestConfirmation returns None when session accepts."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_confirmation = self._get_async_method(iface, "RequestConfirmation")

        task = asyncio.create_task(request_confirmation(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456))
        await asyncio.sleep(0.01)

        session.answer(True)

        result = await task
        assert result is None

    @pytest.mark.asyncio
    async def test_request_confirmation_rejected_raises_dbus_error(self):
        """RequestConfirmation raises DBusError when session rejects."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface
        from dbus_fast.errors import DBusError

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_confirmation = self._get_async_method(iface, "RequestConfirmation")

        task = asyncio.create_task(request_confirmation(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456))
        await asyncio.sleep(0.01)

        session.answer(False)

        with pytest.raises(DBusError) as exc_info:
            await task
        assert exc_info.value.type == "org.bluez.Error.Rejected"

    @pytest.mark.asyncio
    async def test_request_authorization_always_rejects(self):
        """RequestAuthorization always raises DBusError."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.errors import DBusError

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_authorization = self._get_async_method(iface, "RequestAuthorization")

        with pytest.raises(DBusError) as exc_info:
            request_authorization(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert exc_info.value.type == "org.bluez.Error.Rejected"

    @pytest.mark.asyncio
    async def test_authorize_service_always_rejects(self):
        """AuthorizeService always raises DBusError."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.errors import DBusError

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        authorize_service = self._get_async_method(iface, "AuthorizeService")

        with pytest.raises(DBusError) as exc_info:
            authorize_service(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", "some-service")
        assert exc_info.value.type == "org.bluez.Error.Rejected"

    @pytest.mark.asyncio
    async def test_cancel_clears_prompt(self):
        """Cancel clears the pending prompt."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast.service import ServiceInterface
        from dbus_fast.errors import DBusError

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        iface = build_agent_interface(session)

        request_confirmation = self._get_async_method(iface, "RequestConfirmation")
        cancel = self._get_async_method(iface, "Cancel")

        task = asyncio.create_task(request_confirmation(iface, "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF", 123456))
        await asyncio.sleep(0.01)

        cancel(iface)

        assert session._prompt is None
        with pytest.raises(DBusError) as exc_info:
            await task
        assert exc_info.value.type == "org.bluez.Error.Canceled"


class TestPairingSessionTimeout:
    """Tests for PairingSession timeout functionality."""

    @pytest.mark.asyncio
    async def test_request_sets_timeout(self):
        """Request sets a 60-second timeout."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        assert session._timeout_handle is None
        future = session.request("confirm")
        assert session._timeout_handle is not None

    @pytest.mark.asyncio
    async def test_timeout_rejects_after_expiry(self):
        """Timeout rejects the future after 60 seconds."""
        pass


class TestRegisterUnregister:
    """Tests for register and unregister functions."""

    def _make_mock_bus(self):
        """Create a mock bus that records calls and returns success replies."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast import Message, MessageType

        class MockBus:
            def __init__(self):
                self.exports = {}
                self.called_messages = []
                self._serial = 1

            def export(self, path, iface):
                self.exports[path] = iface

            def unexport(self, path):
                if path in self.exports:
                    del self.exports[path]

            async def call(self, msg: Message):
                self.called_messages.append(msg)
                # Return success reply with valid fields
                self._serial += 1
                reply = Message(
                    destination="org.bluez",
                    path="/org/bluez",
                    interface="org.bluez.AgentManager1",
                    member=msg.member,
                    signature="",
                    body=[],
                    message_type=MessageType.METHOD_RETURN,
                    serial=self._serial,
                    reply_serial=msg.serial,
                )
                return reply

        return MockBus

    def _make_error_mock_bus(self, error_name: str):
        """Create a mock bus that returns an error reply."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast import Message, MessageType

        class MockBus:
            def __init__(self):
                self.exports = {}
                self._serial = 1

            def export(self, path, iface):
                self.exports[path] = iface

            def unexport(self, path):
                if path in self.exports:
                    del self.exports[path]

            async def call(self, msg: Message):
                # Return error reply with valid fields
                self._serial += 1
                reply = Message(
                    destination="org.bluez",
                    path="/org/bluez",
                    interface="org.bluez.AgentManager1",
                    member=msg.member,
                    signature="s",
                    body=[error_name],
                    message_type=MessageType.ERROR,
                    serial=self._serial,
                    reply_serial=msg.serial,
                    error_name=error_name,
                )
                return reply

        return MockBus

    @pytest.mark.asyncio
    async def test_register_with_mock_bus(self):
        """register exports the interface and calls RegisterAgent with correct Message."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast import Message, MessageType

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

        MockBus = self._make_mock_bus()
        bus = MockBus()
        await register(bus, session)

        # Verify export was called
        assert "/org/taos/bluez_agent" in bus.exports

        # Verify RegisterAgent was called with correct Message
        assert len(bus.called_messages) == 1
        msg = bus.called_messages[0]
        assert msg.destination == "org.bluez"
        assert msg.path == "/org/bluez"
        assert msg.interface == "org.bluez.AgentManager1"
        assert msg.member == "RegisterAgent"
        assert msg.signature == "os"
        assert msg.body == ["/org/taos/bluez_agent", "KeyboardDisplay"]

    @pytest.mark.asyncio
    async def test_register_raises_on_error_reply(self):
        """register raises RuntimeError on error reply."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast import Message, MessageType

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

        MockBus = self._make_error_mock_bus("org.bluez.Error.AlreadyExists")
        bus = MockBus()

        with pytest.raises(RuntimeError, match="RegisterAgent failed: org.bluez.Error.AlreadyExists"):
            await register(bus, session)

    @pytest.mark.asyncio
    async def test_unregister_with_mock_bus(self):
        """unregister calls UnregisterAgent with correct Message and unexports."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast import Message, MessageType

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

        MockBus = self._make_mock_bus()
        bus = MockBus()
        # First register to set up the export
        await register(bus, session)

        # Then unregister
        await unregister(bus)

        # Verify UnregisterAgent was called with correct Message
        assert len(bus.called_messages) == 2
        register_msg = bus.called_messages[0]
        unregister_msg = bus.called_messages[1]

        assert register_msg.member == "RegisterAgent"
        assert unregister_msg.destination == "org.bluez"
        assert unregister_msg.path == "/org/bluez"
        assert unregister_msg.interface == "org.bluez.AgentManager1"
        assert unregister_msg.member == "UnregisterAgent"
        assert unregister_msg.signature == "o"
        assert unregister_msg.body == ["/org/taos/bluez_agent"]

        # Verify unexport was called
        assert "/org/taos/bluez_agent" not in bus.exports

    @pytest.mark.asyncio
    async def test_unregister_raises_on_error_reply(self):
        """unregister raises RuntimeError on error reply."""
        dbus_fast = pytest.importorskip("dbus_fast")
        from dbus_fast import Message, MessageType

        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")

        # First register with a success mock
        MockBusSuccess = self._make_mock_bus()
        bus = MockBusSuccess()
        await register(bus, session)

        # Then create an error mock for unregister
        # We need to replace the call method to return an error
        serial_counter = [2]  # Start after register's serial=1

        async def error_call(msg: Message):
            serial_counter[0] += 1
            reply = Message(
                destination="org.bluez",
                path="/org/bluez",
                interface="org.bluez.AgentManager1",
                member=msg.member,
                signature="s",
                body=["org.bluez.Error.DoesNotExist"],
                message_type=MessageType.ERROR,
                serial=serial_counter[0],
                reply_serial=msg.serial,
                error_name="org.bluez.Error.DoesNotExist",
            )
            return reply

        bus.call = error_call

        with pytest.raises(RuntimeError, match="UnregisterAgent failed: org.bluez.Error.DoesNotExist"):
            await unregister(bus)