from __future__ import annotations

import asyncio
import re
from typing import Any

_DEVICE_RE = re.compile(r"^/org/bluez/hci[0-9]+/dev_[0-9A-F]{2}(_[0-9A-F]{2}){5}$")


class PairingSession:
    """Pure PairingSession state machine for Bluetooth pairing (taOS S4b)"""

    def __init__(self, device: str):
        if not _DEVICE_RE.fullmatch(device):
            raise ValueError(f"Invalid device path: {device}")
        self.device = device
        self._prompt: dict[str, Any] | None = None
        self._future: asyncio.Future | None = None
        self._timeout_handle: asyncio.Handle | None = None

    def _clear_timeout(self) -> None:
        if self._timeout_handle:
            self._timeout_handle.cancel()
            self._timeout_handle = None

    def _reject_timeout(self) -> None:
        self._timeout_handle = None
        if self._prompt:
            future = self._future
            self._clear_state()
            if future and not future.done():
                future.set_result({"accepted": False, "reason": "timed out"})

    def request(self, kind: str, passkey: int | None = None) -> asyncio.Future:
        """Request a pairing operation. Returns an awaitable answer future."""
        if self._prompt is not None:
            raise RuntimeError("Prompt already pending")

        valid_kinds = {"confirm", "pin", "passkey"}
        if kind not in valid_kinds:
            raise ValueError(f"Invalid kind: {kind}")
        if kind == "passkey" and passkey is not None:
            if not (0 <= passkey <= 999999):
                raise ValueError(f"Passkey must be in range 0..999999, got {passkey}")

        future = asyncio.get_event_loop().create_future()
        self._future = future

        prompt: dict[str, Any] = {"device": self.device, "kind": kind}
        if passkey is not None:
            prompt["passkey"] = passkey
        self._prompt = prompt
        
        # Set up 60-second timeout
        self._timeout_handle = asyncio.get_event_loop().call_later(
            60.0, lambda: self._reject_timeout()
        )
        
        return future

    def answer(self, accept: bool, pin: str | None = None) -> None:
        """Answer a pending prompt. Required for PIN / passkey flows."""
        if self._prompt is None:
            raise RuntimeError("No pending prompt")

        kind = self._prompt["kind"]
        if not accept:
            future = self._future
            self._clear_state()
            if future and not future.done():
                future.set_result({"accepted": False, "reason": "rejected"})
            return

        if kind == "pin":
            if pin is None:
                raise ValueError("PIN required for PIN kind")
            if not re.fullmatch(r"[0-9]{4,16}", pin):
                raise ValueError("PIN must match regex ^[0-9]{4,16}$")
            future = self._future
            self._clear_state()
            if future and not future.done():
                future.set_result({"accepted": True, "pin": pin})
            return

        if kind == "passkey":
            passkey = self._prompt.get("passkey")
            if passkey is None:
                raise ValueError("Passkey required for passkey kind")
            future = self._future
            self._clear_state()
            if future and not future.done():
                future.set_result({"accepted": True, "passkey": passkey})
            return

        if kind == "confirm":
            future = self._future
            self._clear_state()
            if future and not future.done():
                future.set_result({"accepted": True})
            return

        raise RuntimeError(f"Unhandled kind: {kind}")

    def cancel(self) -> None:
        """Cancel a pending prompt."""
        if self._prompt is None:
            return

        future = self._future
        self._clear_state()
        if future and not future.done():
            future.set_exception(Exception("org.bluez.Error.Canceled"))

    def _clear_state(self) -> None:
        self._clear_timeout()
        self._prompt = None
        self._future = None


def build_agent_interface(session: PairingSession) -> Any:
    """Build a dbus_fast ServiceInterface for org.bluez.Agent1 based on a PairingSession."""
    import dbus_fast

    class Agent1Interface(dbus_fast.service.ServiceInterface):
        def __init__(self, session: PairingSession):
            super().__init__("org.bluez.Agent1")
            self.session = session

        def Release(self, object_path: str) -> str:
            """Release the agent."""
            self.session._clear_state()
            return ""

        def RequestPinCode(self, object_path: str) -> str:
            """Request a PIN code for the device."""
            if object_path != self.session.device:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected", 
                    f"Request for device {object_path} not allowed"
                )
            if self.session._prompt is not None:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    "Already awaiting response for another request"
                )
            future = self.session.request("pin")
            result = future.result(timeout=5.0)
            return result["pin"]

        def DisplayPinCode(self, object_path: str, pin: str) -> None:
            """Display PIN code for user confirmation."""
            if object_path != self.session.device:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    f"Display for device {object_path} not allowed"
                )

        def RequestPasskey(self, object_path: str) -> int:
            """Request a passkey for the device."""
            if object_path != self.session.device:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    f"Request for device {object_path} not allowed"
                )
            if self.session._prompt is not None:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    "Already awaiting response for another request"
                )
            future = self.session.request("passkey")
            result = future.result(timeout=5.0)
            return result["passkey"]

        def DisplayPasskey(self, object_path: str, passkey: int, confirm: bool) -> None:
            """Display passkey for user confirmation."""
            if object_path != self.session.device:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    f"Display for device {object_path} not allowed"
                )

        def RequestConfirmation(self, object_path: str, passkey: int) -> None:
            """Request confirmation for a passkey."""
            if object_path != self.session.device:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    f"Request for device {object_path} not allowed"
                )
            if self.session._prompt is not None:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    "Already awaiting response for another request"
                )
            future = self.session.request("confirm")
            try:
                result = future.result(timeout=5.0)
            except Exception as e:
                raise dbus_fast.errors.DBusError(
                    "org.bluez.Error.Rejected",
                    f"Request rejected: {e}"
                )

        def RequestAuthorization(self, object_path: str) -> None:
            """Request authorization for a device. Always rejects."""
            raise dbus_fast.errors.DBusError(
                "org.bluez.Error.Rejected",
                "Authorization not allowed"
            )

        def AuthorizeService(self, object_path: str, service: str) -> None:
            """Authorize a service. Always rejects."""
            raise dbus_fast.errors.DBusError(
                "org.bluez.Error.Rejected",
                "Service authorization not allowed"
            )

        def Cancel(self) -> None:
            """Cancel the current request."""
            if self.session._prompt is not None:
                self.session.cancel()

    return Agent1Interface(session)


async def register(bus: Any, session: PairingSession, path: str = "/org/taos/bluez_agent") -> None:
    """Register the agent with the BlueZ AgentManager1."""
    iface = build_agent_interface(session)
    bus.export(path, iface)
    
    await bus.call(
        "org.bluez.AgentManager1",
        "/org/bluez",
        "RegisterAgent",
        [path, "KeyboardDisplay"]
    )


async def unregister(bus: Any, path: str = "/org/taos/bluez_agent") -> None:
    """Unregister the agent from BlueZ AgentManager1."""
    await bus.call(
        "org.bluez.AgentManager1",
        "/org/bluez", 
        "UnregisterAgent",
        [path]
    )
    bus.unexport(path)