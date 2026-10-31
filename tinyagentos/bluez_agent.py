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