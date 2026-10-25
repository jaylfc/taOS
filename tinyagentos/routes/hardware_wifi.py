from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends

from tinyagentos.auth_context import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


async def _busctl(*args: str) -> tuple[int, object | None]:
    """Run busctl --json=short and return (rc, parsed JSON or None). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "busctl",
            "--json=short",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        rc = proc.returncode if proc.returncode is not None else 1
        if rc != 0:
            return rc, None
        try:
            data = json.loads((out or b"").decode(errors="replace"))
            return rc, data
        except Exception:
            return 1, None
    except FileNotFoundError:
        return 127, None
    except asyncio.TimeoutError:
        return 124, None
    except Exception:
        return 1, None


def _get_data(val: object | None) -> object | None:
    if isinstance(val, dict) and "data" in val:
        return val.get("data")
    return val


@router.get("/api/hardware/wifi", dependencies=[Depends(require_admin)])
async def get_wifi():
    """Read Wi-Fi state and access points via NetworkManager D-Bus."""
    rc, val = await _busctl(
        "get-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
    )
    if rc != 0:
        return {"available": False, "wifi_enabled": None, "devices": [], "access_points": []}

    wifi_enabled = _get_data(val)
    rc, devices_data = await _busctl(
        "call",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "GetDevices",
    )
    if rc != 0 or devices_data is None:
        device_paths = []
    else:
        d = _get_data(devices_data)
        if isinstance(d, list) and len(d) > 0 and isinstance(d[0], list):
            device_paths = [p for p in d[0] if isinstance(p, str)]
        elif isinstance(d, list):
            device_paths = [p for p in d if isinstance(p, str)]
        else:
            device_paths = []

    devices_out = []
    aps_out = []

    for path in device_paths:
        rc_dt, dt_val = await _busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device",
            "DeviceType",
        )
        if rc_dt != 0:
            continue
        dt = _get_data(dt_val)
        if dt != 2:
            continue
        rc_if, if_val = await _busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device",
            "Interface",
        )
        rc_st, st_val = await _busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device",
            "State",
        )
        iface = _get_data(if_val) if rc_if == 0 else None
        state = _get_data(st_val) if rc_st == 0 else None
        if not isinstance(iface, str):
            continue
        if not isinstance(state, int):
            try:
                if state is not None:
                    state = int(state)
            except Exception:
                state = None
        devices_out.append({"path": path, "iface": iface, "state": state})

        rc_ap, ap_val = await _busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device.Wireless",
            "ActiveAccessPoint",
        )
        active_ap = _get_data(ap_val) if rc_ap == 0 else None
        if not isinstance(active_ap, str):
            active_ap = None

        rc_aps, aps_val = await _busctl(
            "call",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device.Wireless",
            "GetAllAccessPoints",
        )
        if rc_aps != 0 or aps_val is None:
            continue
        ap_list_data = _get_data(aps_val)
        if isinstance(ap_list_data, list) and len(ap_list_data) > 0 and isinstance(ap_list_data[0], list):
            ap_paths = [p for p in ap_list_data[0] if isinstance(p, str)]
        elif isinstance(ap_list_data, list):
            ap_paths = [p for p in ap_list_data if isinstance(p, str)]
        else:
            ap_paths = []

        for ap_path in ap_paths:
            try:
                rc_ssid, ssid_val = await _busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "Ssid",
                )
                if rc_ssid != 0:
                    continue
                ssid_data = _get_data(ssid_val)
                if isinstance(ssid_data, list):
                    try:
                        ssid = bytes([int(b) for b in ssid_data]).decode("utf-8", errors="replace")
                    except Exception:
                        continue
                else:
                    continue
                if not ssid:
                    continue
                rc_str, str_val = await _busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "Strength",
                )
                if rc_str != 0:
                    continue
                strength = _get_data(str_val)
                rc_freq, freq_val = await _busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "Frequency",
                )
                if rc_freq != 0:
                    continue
                frequency = _get_data(freq_val)
                rc_wpa, wpa_val = await _busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "WpaFlags",
                )
                rc_rsn, rsn_val = await _busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "RsnFlags",
                )
                wpa_flags = _get_data(wpa_val) if rc_wpa == 0 else 0
                rsn_flags = _get_data(rsn_val) if rc_rsn == 0 else 0
                try:
                    wpa_flags_i = int(wpa_flags) if wpa_flags is not None else 0
                except Exception:
                    wpa_flags_i = 0
                try:
                    rsn_flags_i = int(rsn_flags) if rsn_flags is not None else 0
                except Exception:
                    rsn_flags_i = 0
                secured = (wpa_flags_i != 0) or (rsn_flags_i != 0)
                active = active_ap == ap_path if active_ap is not None else False
                try:
                    strength_i = int(strength) if strength is not None else 0
                except Exception:
                    strength_i = 0
                try:
                    frequency_i = int(frequency) if frequency is not None else 0
                except Exception:
                    frequency_i = 0
                aps_out.append(
                    {
                        "ssid": ssid,
                        "strength": strength_i,
                        "frequency": frequency_i,
                        "secured": secured,
                        "active": active,
                    }
                )
            except Exception:
                continue

    aps_out.sort(key=lambda x: x.get("strength", 0), reverse=True)
    return {
        "available": True,
        "wifi_enabled": wifi_enabled,
        "devices": devices_out,
        "access_points": aps_out,
    }
