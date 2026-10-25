from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from tinyagentos.auth_context import require_admin
from tinyagentos.dbus_cli import busctl

logger = logging.getLogger(__name__)

router = APIRouter()


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
            if address:
                adapters.append({"path": path, "address": address, "powered": powered})

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
