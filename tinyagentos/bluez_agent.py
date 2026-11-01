import asyncio
import re
from typing import Any

try:
    from dbus_fast.annotations import DBusSignature
    from typing import Annotated

    ObjPath = Annotated[str, DBusSignature("o")]
    U32 = Annotated[int, DBusSignature("u")]
    U16 = Annotated[int, DBusSignature("q")]
    Str = Annotated[str, DBusSignature("s")]
except ImportError:
    ObjPath = str
    U32 = int
    U16 = int
    Str = str

_DEVICE_RE = re.compile(r"^/org/bluez/hci[0-9]+/dev_[0-9A-F]{2}(_[0-9A-F]{2}){5}$")

PROMPT_TIMEOUT_S = 60.0


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

        # Set up timeout
        self._timeout_handle = asyncio.get_event_loop().call_later(
            PROMPT_TIMEOUT_S, lambda: self._reject_timeout()
        )

        return future

    def answer(self, accept: bool, pin: str | None = None, passkey: int | None = None) -> None:
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
            if not re.fullmatch(r"[\x21-\x7e]{1,16}", pin):
                raise ValueError("PIN must be 1 to 16 printable ASCII characters")
            future = self._future
            self._clear_state()
            if future and not future.done():
                future.set_result({"accepted": True, "pin": pin})
            return

        if kind == "passkey":
            if passkey is None:
                raise ValueError("Passkey required for passkey kind")
            if not (0 <= passkey <= 999999):
                raise ValueError(f"Passkey must be in range 0..999999, got {passkey}")
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
    try:
        import dbus_fast
        from dbus_fast.service import ServiceInterface, dbus_method
        from dbus_fast.errors import DBusError
    except ImportError:
        class _DummyServiceInterface:
            pass
        return _DummyServiceInterface()

    class Agent1Interface(ServiceInterface):
        def __init__(self, session: PairingSession):
            super().__init__("org.bluez.Agent1")
            self.session = session

        @dbus_method()
        def Release(self) -> None:
            """Release the agent."""
            self.session.cancel()

        @dbus_method()
        async def RequestPinCode(self, object_path: ObjPath) -> Str:
            """Request a PIN code for the device."""
            if object_path != self.session.device:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    f"Request for device {object_path} not allowed"
                )
            if self.session._prompt is not None:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    "Already awaiting response for another request"
                )
            future = self.session.request("pin")
            try:
                result = await asyncio.wait_for(future, timeout=PROMPT_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.session._clear_state()
                raise DBusError("org.bluez.Error.Rejected", "Request timed out")
            except asyncio.CancelledError:
                self.session._clear_state()
                raise DBusError("org.bluez.Error.Canceled", "Request canceled")
            except Exception as e:
                if "org.bluez.Error.Canceled" in str(e):
                    self.session._clear_state()
                    raise DBusError("org.bluez.Error.Canceled", "Request canceled")
                raise
            if not result.get("accepted", False):
                raise DBusError("org.bluez.Error.Rejected", result.get("reason", "rejected"))
            return result["pin"]

        @dbus_method()
        def DisplayPinCode(self, object_path: ObjPath, pin: Str) -> None:
            """Display PIN code for user confirmation."""
            if object_path != self.session.device:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    f"Display for device {object_path} not allowed"
                )

        @dbus_method()
        async def RequestPasskey(self, object_path: ObjPath) -> U32:
            """Request a passkey for the device."""
            if object_path != self.session.device:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    f"Request for device {object_path} not allowed"
                )
            if self.session._prompt is not None:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    "Already awaiting response for another request"
                )
            future = self.session.request("passkey")
            try:
                result = await asyncio.wait_for(future, timeout=PROMPT_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.session._clear_state()
                raise DBusError("org.bluez.Error.Rejected", "Request timed out")
            except asyncio.CancelledError:
                self.session._clear_state()
                raise DBusError("org.bluez.Error.Canceled", "Request canceled")
            except Exception as e:
                if "org.bluez.Error.Canceled" in str(e):
                    self.session._clear_state()
                    raise DBusError("org.bluez.Error.Canceled", "Request canceled")
                raise
            if not result.get("accepted", False):
                raise DBusError("org.bluez.Error.Rejected", result.get("reason", "rejected"))
            return result["passkey"]

        @dbus_method()
        def DisplayPasskey(self, object_path: ObjPath, passkey: U32, entered: U16) -> None:
            """Display passkey for user confirmation."""
            if object_path != self.session.device:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    f"Display for device {object_path} not allowed"
                )

        @dbus_method()
        async def RequestConfirmation(self, object_path: ObjPath, passkey: U32) -> None:
            """Request confirmation for a passkey."""
            if object_path != self.session.device:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    f"Request for device {object_path} not allowed"
                )
            if self.session._prompt is not None:
                raise DBusError(
                    "org.bluez.Error.Rejected",
                    "Already awaiting response for another request"
                )
            future = self.session.request("confirm", passkey)
            try:
                result = await asyncio.wait_for(future, timeout=PROMPT_TIMEOUT_S)
            except asyncio.TimeoutError:
                self.session._clear_state()
                raise DBusError("org.bluez.Error.Rejected", "Request timed out")
            except asyncio.CancelledError:
                self.session._clear_state()
                raise DBusError("org.bluez.Error.Canceled", "Request canceled")
            except Exception as e:
                if "org.bluez.Error.Canceled" in str(e):
                    self.session._clear_state()
                    raise DBusError("org.bluez.Error.Canceled", "Request canceled")
                raise
            if not result.get("accepted", False):
                raise DBusError("org.bluez.Error.Rejected", result.get("reason", "rejected"))

        @dbus_method()
        def RequestAuthorization(self, object_path: ObjPath) -> None:
            """Request authorization for a device. Always rejects."""
            raise DBusError(
                "org.bluez.Error.Rejected",
                "Authorization not allowed"
            )

        @dbus_method()
        def AuthorizeService(self, object_path: ObjPath, service: Str) -> None:
            """Authorize a service. Always rejects."""
            raise DBusError(
                "org.bluez.Error.Rejected",
                "Service authorization not allowed"
            )

        @dbus_method()
        def Cancel(self) -> None:
            """Cancel the current request."""
            if self.session._prompt is not None:
                self.session.cancel()

    return Agent1Interface(session)


async def register(bus: Any, session: PairingSession, path: str = "/org/taos/bluez_agent") -> None:
    """Register the agent with the BlueZ AgentManager1."""
    try:
        from dbus_fast import Message, MessageType
    except ImportError:
        return

    iface = build_agent_interface(session)
    bus.export(path, iface)

    msg = Message(
        destination="org.bluez",
        path="/org/bluez",
        interface="org.bluez.AgentManager1",
        member="RegisterAgent",
        signature="os",
        body=[path, "KeyboardDisplay"],
    )
    reply = await bus.call(msg)
    if reply.message_type == MessageType.ERROR:
        bus.unexport(path)
        raise RuntimeError(f"RegisterAgent failed: {reply.error_name}")


async def unregister(bus: Any, path: str = "/org/taos/bluez_agent") -> None:
    """Unregister the agent from BlueZ AgentManager1."""
    try:
        from dbus_fast import Message, MessageType
    except ImportError:
        return

    msg = Message(
        destination="org.bluez",
        path="/org/bluez",
        interface="org.bluez.AgentManager1",
        member="UnregisterAgent",
        signature="o",
        body=[path],
    )
    reply = await bus.call(msg)
    if reply.message_type == MessageType.ERROR:
        raise RuntimeError(f"UnregisterAgent failed: {reply.error_name}")
    bus.unexport(path)