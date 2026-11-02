from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_bus: Optional["MessageBus"] = None
_bus_lock = asyncio.Lock()


class BusUnavailable(Exception):
    """Raised when the system bus is unavailable or dbus-fast cannot be imported."""
    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


async def get_system_bus() -> "MessageBus":
    """Return a shared system bus connection, creating it lazily under a lock.
    Reconnects if the previous connection is disconnected.
    """
    global _bus
    async with _bus_lock:
        if _bus is not None and _bus.connected:
            return _bus

        try:
            from dbus_fast import BusType
            from dbus_fast.aio import MessageBus
        except Exception as e:
            logger.exception("dbus-fast import failed")
            raise BusUnavailable("dbus_fast_missing") from e

        try:
            bus = await asyncio.wait_for(
                MessageBus(bus_type=BusType.SYSTEM).connect(),
                timeout=5.0,
            )
            _bus = bus
            return bus
        except asyncio.TimeoutError as e:
            logger.exception("Timed out connecting to system bus")
            raise BusUnavailable("bluez_unavailable") from e
        except Exception as e:
            logger.exception("Failed to connect to system bus")
            raise BusUnavailable("bluez_unavailable") from e


async def close_system_bus() -> None:
    """Disconnect the shared system bus connection. Idempotent."""
    global _bus
    async with _bus_lock:
        if _bus is not None:
            try:
                _bus.disconnect()
            except Exception:
                pass
            _bus = None
