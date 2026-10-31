from __future__ import annotations

import asyncio
import logging
import re
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.auth_context import require_admin
from tinyagentos.bluez_bus import BusUnavailable, get_system_bus
from tinyagentos.dbus_cli import busctl

logger = logging.getLogger(__name__)

router = APIRouter()

_ADAPTER_RE = re.compile(r"^/org/bluez/hci[0-9]+$")
_DEVICE_RE = re.compile(r"^/org/bluez/hci[0-9]+/dev_[0-9A-F]{2}(_[0-9A-F]{2}){5}$")


class BtPowerBody(BaseModel):
    adapter: str = Field(..., description="BlueZ adapter path (e.g., /org/bluez/hci0)")
    powered: bool = Field(..., description="Power state to set")


class BtDeviceBody(BaseModel):
    path: str = Field(..., description="BlueZ device path")
    action: Literal["connect", "disconnect", "pair", "trust", "untrust", "remove"] = Field(
        ..., description="Action to perform on device"
    )


class BtDiscoveryBody(BaseModel):
    adapter: str = Field(..., description="BlueZ adapter path")
    on: bool = Field(..., description="Start or stop discovery")


_discovery_timers: dict[str, asyncio.Task] = {}


def _get_data(val: object | None) -> object | None:
    if isinstance(val, dict) and "data" in val:
        return val.get("data")
    return val


def _get_bool(val: object | None) -> bool:
    data = _get_data(val)
    if isinstance(data, bool):
        return data
    return False


def _get_str(val: object | None) -> str | None:
    data = _get_data(val)
    if isinstance(data, str):
        return data
    return None


@router.get("/api/hardware/bluetooth", dependencies=[Depends(require_admin)])
async def get_bluetooth():
    """Read Bluetooth adapters and devices via BlueZ D-Bus."""
    rc, val = await busctl(
        "call",
        "org.bluez",
        "/",
        "org.freedesktop.DBus.ObjectManager",
        "GetManagedObjects",
    )
    if rc != 0 or val is None:
        return {"available": False, "adapters": [], "devices": []}

    data = _get_data(val)
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        return {"available": True, "adapters": [], "devices": []}

    adapters = []
    devices = []

    for path, interfaces in data.items():
        if not isinstance(interfaces, dict):
            continue

        # Check for Adapter1
        adapter1 = interfaces.get("org.bluez.Adapter1")
        if isinstance(adapter1, dict):
            address = _get_str(adapter1.get("Address"))
            powered = _get_bool(adapter1.get("Powered"))
            discovering = _get_bool(adapter1.get("Discovering"))
            if address:
                adapters.append({"path": path, "address": address, "powered": powered, "discovering": discovering})

        # Check for Device1
        device1 = interfaces.get("org.bluez.Device1")
        if isinstance(device1, dict):
            address = _get_str(device1.get("Address"))
            if not address:
                continue

            # name = Alias if present else Name else Address
            name = _get_str(device1.get("Alias"))
            if name is None:
                name = _get_str(device1.get("Name"))
            if name is None:
                name = address

            paired = _get_bool(device1.get("Paired"))
            connected = _get_bool(device1.get("Connected"))
            trusted = _get_bool(device1.get("Trusted"))

            devices.append(
                {
                    "path": path,
                    "address": address,
                    "name": name,
                    "paired": paired,
                    "connected": connected,
                    "trusted": trusted,
                }
            )

    # Sort devices: connected first, then paired, then by name
    devices.sort(key=lambda d: (not d["connected"], not d["paired"], d["name"] or ""))

    return {"available": True, "adapters": adapters, "devices": devices}


@router.post("/api/hardware/bluetooth/power", dependencies=[Depends(require_admin)])
async def power_bluetooth_adapter(body: BtPowerBody):
    """Power the Bluetooth adapter on or off."""
    if not _ADAPTER_RE.fullmatch(body.adapter):
        return JSONResponse({"error": "invalid_path"}, status_code=422)

    powered = "true" if body.powered else "false"
    rc, _ = await busctl(
        "set-property",
        "org.bluez",
        body.adapter,
        "org.bluez.Adapter1",
        "Powered",
        "b",
        powered,
    )

    if rc == 0:
        return {"ok": True}
    if rc == 127:
        return JSONResponse({"error": "bluez_unavailable"}, status_code=503)
    if rc == 124:
        return JSONResponse({"error": "timeout"}, status_code=504)

    return JSONResponse({"error": "write_failed"}, status_code=502)


@router.post("/api/hardware/bluetooth/device", dependencies=[Depends(require_admin)])
async def device_bluetooth_action(body: BtDeviceBody):
    """Perform connect/disconnect/pair/trust/untrust/remove on a device."""
    if not _DEVICE_RE.fullmatch(body.path):
        return JSONResponse({"error": "invalid_path"}, status_code=422)

    adapter_path = body.path.rsplit("/", 1)[0]

    if body.action == "connect":
        rc, _ = await busctl("call", "org.bluez", body.path, "org.bluez.Device1", "Connect")
    elif body.action == "disconnect":
        rc, _ = await busctl(
            "call", "org.bluez", body.path, "org.bluez.Device1", "Disconnect"
        )
    elif body.action == "pair":
        rc, _ = await busctl("call", "org.bluez", body.path, "org.bluez.Device1", "Pair")
    elif body.action == "trust":
        rc, _ = await busctl(
            "set-property",
            "org.bluez",
            body.path,
            "org.bluez.Device1",
            "Trusted",
            "b",
            "true",
        )
    elif body.action == "untrust":
        rc, _ = await busctl(
            "set-property",
            "org.bluez",
            body.path,
            "org.bluez.Device1",
            "Trusted",
            "b",
            "false",
        )
    elif body.action == "remove":
        rc, _ = await busctl(
            "call",
            "org.bluez",
            adapter_path,
            "org.bluez.Adapter1",
            "RemoveDevice",
            "o",
            body.path,
        )
    else:
        return JSONResponse({"error": "invalid_action"}, status_code=400)

    if rc == 0:
        return {"ok": True}
    if rc == 127:
        return JSONResponse({"error": "bluez_unavailable"}, status_code=503)
    if rc == 124:
        return JSONResponse({"error": "timeout"}, status_code=504)

    return JSONResponse({"error": "write_failed"}, status_code=502)


async def _stop_discovery_after_delay(adapter: str, delay: float = 60.0) -> None:
    """Stop discovery on an adapter after a delay."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    try:
        bus = await get_system_bus()
        from dbus_fast import Message
        reply = await bus.call(
            Message(
                destination="org.bluez",
                path=adapter,
                interface="org.bluez.Adapter1",
                member="StopDiscovery",
            )
        )
        if reply.message_type == Message.MessageType.ERROR:
            logger.warning("StopDiscovery error on %s: %s", adapter, reply.error_name)
    except Exception:
        logger.exception("Failed to stop discovery on %s after delay", adapter)
    finally:
        _discovery_timers.pop(adapter, None)


@router.post("/api/hardware/bluetooth/discovery", dependencies=[Depends(require_admin)])
async def discovery_bluetooth(body: BtDiscoveryBody):
    """Start or stop Bluetooth discovery on an adapter.
    
    When starting (on=true), schedules a StopDiscovery after 60 seconds.
    When stopping (on=false), cancels the timer and calls StopDiscovery immediately.
    Returns the read-back Discovering property.
    """
    if not _ADAPTER_RE.fullmatch(body.adapter):
        return JSONResponse({"error": "invalid_path"}, status_code=422)

    adapter = body.adapter

    if body.on:
        # Cancel any existing timer for this adapter
        existing_timer = _discovery_timers.pop(adapter, None)
        if existing_timer is not None:
            existing_timer.cancel()

        try:
            bus = await get_system_bus()
        except BusUnavailable as e:
            error_code = str(e)
            if error_code == "dbus_fast_missing":
                return JSONResponse({"error": "dbus_fast_missing"}, status_code=503)
            return JSONResponse({"error": "bluez_unavailable"}, status_code=503)
        except Exception:
            logger.exception("Failed to get system bus for discovery")
            return JSONResponse({"error": "bluez_unavailable"}, status_code=503)

        try:
            from dbus_fast import Message, MessageType
            reply = await bus.call(
                Message(
                    destination="org.bluez",
                    path=adapter,
                    interface="org.bluez.Adapter1",
                    member="StartDiscovery",
                )
            )
            if reply.message_type == MessageType.ERROR:
                if reply.error_name != "org.bluez.Error.InProgress":
                    return JSONResponse(
                        {"error": "write_failed", "dbus_error": reply.error_name},
                        status_code=502,
                    )
        except Exception:
            logger.exception("StartDiscovery call failed")
            return JSONResponse({"error": "write_failed"}, status_code=502)

        # Schedule stop after 60 seconds
        timer = asyncio.create_task(_stop_discovery_after_delay(adapter, 60.0))
        _discovery_timers[adapter] = timer

        # Read back Discovering property
        try:
            from dbus_fast import Message as Message2, MessageType as MessageType2
            reply = await bus.call(
                Message2(
                    destination="org.bluez",
                    path=adapter,
                    interface="org.freedesktop.DBus.Properties",
                    member="Get",
                    signature="ss",
                    body=["org.bluez.Adapter1", "Discovering"],
                )
            )
            discovering = False
            if reply.message_type == MessageType2.METHOD_RETURN and reply.body:
                discovering = bool(reply.body[0])
        except Exception:
            logger.exception("Failed to read Discovering property")
            discovering = True

        return {"ok": True, "discovering": discovering}

    else:
        # Stop discovery
        timer = _discovery_timers.pop(adapter, None)
        if timer is not None:
            timer.cancel()

        try:
            bus = await get_system_bus()
        except BusUnavailable as e:
            error_code = str(e)
            if error_code == "dbus_fast_missing":
                return JSONResponse({"error": "dbus_fast_missing"}, status_code=503)
            return JSONResponse({"error": "bluez_unavailable"}, status_code=503)
        except Exception:
            logger.exception("Failed to get system bus for discovery stop")
            return JSONResponse({"error": "bluez_unavailable"}, status_code=503)

        try:
            from dbus_fast import Message
            await bus.call(
                Message(
                    destination="org.bluez",
                    path=adapter,
                    interface="org.bluez.Adapter1",
                    member="StopDiscovery",
                )
            )
        except Exception:
            logger.exception("StopDiscovery call failed")
            return JSONResponse({"error": "write_failed"}, status_code=502)

        return {"ok": True, "discovering": False}