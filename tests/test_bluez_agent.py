"""BlueZ pairing agent module tests (taOS S4b)"""

from __future__ import annotations

import pytest

from tinyagentos.bluez_agent import PairingSession


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

    def test_request_sets_prompt(self):
        """Request for confirm sets prompt."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        assert session._prompt == {
            "device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "kind": "confirm",
        }
        assert session._future is future
        assert not future.done()

    def test_request_with_pin_sets_prompt(self):
        """Request for PIN sets prompt with device and kind."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        assert session._prompt == {
            "device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "kind": "pin",
        }

    def test_request_with_passkey_sets_prompt(self):
        """Request for passkey sets prompt with passkey value."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("passkey", 123456)
        assert session._prompt == {
            "device": "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF",
            "kind": "passkey",
            "passkey": 123456,
        }

    def test_request_validates_kind(self):
        """Request validates kind parameter."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        with pytest.raises(ValueError, match="Invalid kind"):
            session.request("invalid")

    def test_request_validates_passkey_range(self):
        """Request validates passkey is within range."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        with pytest.raises(ValueError, match="range 0..999999"):
            session.request("passkey", -1)
        with pytest.raises(ValueError, match="range 0..999999"):
            session.request("passkey", 1000000)

    def test_answer_accepts_confirm(self):
        """Answer with accept True for confirm completes future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.answer(True)
        assert future.result() == {"accepted": True}
        assert session._prompt is None

    def test_answer_rejects_confirm(self):
        """Answer with accept False for confirm rejects."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.answer(False)
        assert future.result() == {"accepted": False, "reason": "rejected"}
        assert session._prompt is None

    def test_answer_accepts_pin(self):
        """Answer with PIN completes future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        session.answer(True, "1234")
        assert future.result() == {"accepted": True, "pin": "1234"}
        assert session._prompt is None

    def test_answer_rejects_pin(self):
        """Answer with reject for PIN completes future with rejection."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        session.answer(False)
        assert future.result() == {"accepted": False, "reason": "rejected"}
        assert session._prompt is None

    def test_answer_accepts_passkey(self):
        """Answer with passkey completes future."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("passkey", 123456)
        session.answer(True)
        assert future.result() == {"accepted": True, "passkey": 123456}
        assert session._prompt is None

    def test_answer_rejects_passkey(self):
        """Answer with reject for passkey completes future with rejection."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("passkey", 123456)
        session.answer(False)
        assert future.result() == {"accepted": False, "reason": "rejected"}
        assert session._prompt is None

    def test_answer_requires_pin_for_pin_kind(self):
        """Answer requires PIN for PIN kind."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        with pytest.raises(ValueError, match="PIN required"):
            session.answer(True, None)

    def test_answer_requires_pin_validation(self):
        """Answer validates PIN format."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("pin")
        with pytest.raises(ValueError, match="PIN must match regex"):
            session.answer(True, "abc")
        with pytest.raises(ValueError, match="PIN must match regex"):
            session.answer(True, "1")
        session.answer(True, "1234")
        assert future.result() == {"accepted": True, "pin": "1234"}

    def test_answer_requires_passkey_in_prompt(self):
        """Answer requires passkey to be in prompt for passkey kind."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        # Request without passkey
        future = session.request("passkey")
        with pytest.raises(ValueError, match="Passkey required"):
            session.answer(True)

    def test_answer_no_pending_prompt_raises(self):
        """Answer without pending prompt raises error."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        with pytest.raises(RuntimeError, match="No pending prompt"):
            session.answer(True)

    def test_answer_twice_after_completion_raises(self):
        """Answer after prompt completed raises error."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future = session.request("confirm")
        session.answer(True)
        with pytest.raises(RuntimeError, match="No pending prompt"):
            session.answer(False)

    def test_cancel_clears_prompt(self):
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

    def test_request_twice_raises(self):
        """Request while prompt is pending raises error."""
        session = PairingSession("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF")
        future1 = session.request("confirm")
        with pytest.raises(RuntimeError, match="Prompt already pending"):
            session.request("pin")