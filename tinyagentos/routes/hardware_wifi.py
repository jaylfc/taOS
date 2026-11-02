from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import require_admin
from tinyagentos.dbus_cli import busctl

logger = logging.getLogger(__name__)

router = APIRouter()


class WifiEnabledBody(BaseModel):
    enabled: bool


class WifiConnectBody(BaseModel):
    ssid: str
    psk: str | None = None
    iface: str | None = None


class WifiDisconnectBody(BaseModel):
    iface: str


class WifiScanBody(BaseModel):
    iface: str | None = None


class WifiForgetBody(BaseModel):
    ssid: str


def _get_data(val: object | None) -> object | None:
    if isinstance(val, dict) and "data" in val:
        return val.get("data")
    return val


def _unwrap_variant(v: object) -> object:
    """Unwrap a busctl typed variant leaf {"type": "...", "data": ...} to its data value."""
    if isinstance(v, dict) and set(v.keys()) == {"type", "data"}:
        return v["data"]
    return v


async def _wifi_devices() -> list[tuple[str, str]] | None:
    """Return (device_path, iface) for every Wi-Fi device (DeviceType == 2).
    Returns None if GetDevices call fails (NetworkManager unavailable).
    """
    rc, devices_data = await busctl(
        "call",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "GetDevices",
    )
    if rc != 0 or devices_data is None:
        return None
    d = _get_data(devices_data)
    if isinstance(d, list) and len(d) > 0 and isinstance(d[0], list):
        device_paths = [p for p in d[0] if isinstance(p, str)]
    elif isinstance(d, list):
        device_paths = [p for p in d if isinstance(p, str)]
    else:
        device_paths = []

    out: list[tuple[str, str]] = []
    for path in device_paths:
        rc_dt, dt_val = await busctl(
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
        rc_if, if_val = await busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device",
            "Interface",
        )
        iface = _get_data(if_val) if rc_if == 0 else None
        if isinstance(iface, str):
            out.append((path, iface))
    return out


@router.get("/api/hardware/wifi", dependencies=[Depends(require_admin)])
async def get_wifi():
    """Read Wi-Fi state and access points via NetworkManager D-Bus."""
    rc, val = await busctl(
        "get-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
    )
    if rc != 0:
        return {"available": False, "wifi_enabled": None, "devices": [], "access_points": []}

    wifi_enabled = _get_data(val)
    rc, devices_data = await busctl(
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
        rc_dt, dt_val = await busctl(
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
        rc_if, if_val = await busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device",
            "Interface",
        )
        rc_st, st_val = await busctl(
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

        rc_ap, ap_val = await busctl(
            "get-property",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Device.Wireless",
            "ActiveAccessPoint",
        )
        active_ap = _get_data(ap_val) if rc_ap == 0 else None
        if not isinstance(active_ap, str):
            active_ap = None

        rc_aps, aps_val = await busctl(
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
                rc_ssid, ssid_val = await busctl(
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
                rc_str, str_val = await busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "Strength",
                )
                if rc_str != 0:
                    continue
                strength = _get_data(str_val)
                rc_freq, freq_val = await busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "Frequency",
                )
                if rc_freq != 0:
                    continue
                frequency = _get_data(freq_val)
                rc_wpa, wpa_val = await busctl(
                    "get-property",
                    "org.freedesktop.NetworkManager",
                    ap_path,
                    "org.freedesktop.NetworkManager.AccessPoint",
                    "WpaFlags",
                )
                rc_rsn, rsn_val = await busctl(
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


def _nm_unavailable_response() -> JSONResponse:
    return JSONResponse({"error": "networkmanager_unavailable"}, status_code=503)


def _write_failed_response() -> JSONResponse:
    return JSONResponse(
        {
            "error": "write_failed",
            "hint": "Wi-Fi changes need local mode enabled (polkit rule 50-taos-hardware.rules)",
        },
        status_code=502,
    )


def _scan_failed_response() -> JSONResponse:
    return JSONResponse({"error": "scan_failed"}, status_code=502)


@router.post("/api/hardware/wifi/enabled", dependencies=[Depends(require_admin)])
async def set_wifi_enabled(body: WifiEnabledBody):
    rc, _ = await busctl(
        "get-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
    )
    if rc != 0:
        return _nm_unavailable_response()

    enabled_str = "true" if body.enabled else "false"
    rc, _ = await busctl(
        "set-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
        "b",
        enabled_str,
    )
    if rc != 0:
        return _write_failed_response()
    return {"ok": True, "wifi_enabled": body.enabled}


@router.post("/api/hardware/wifi/disconnect", dependencies=[Depends(require_admin)])
async def wifi_disconnect(body: WifiDisconnectBody):
    rc, _ = await busctl(
        "get-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
    )
    if rc != 0:
        return _nm_unavailable_response()

    devices = await _wifi_devices()
    if devices is None:
        return _nm_unavailable_response()
    device_path = None
    for path, iface in devices:
        if iface == body.iface:
            device_path = path
            break
    if device_path is None:
        return JSONResponse({"error": "unknown_iface"}, status_code=404)

    rc, _ = await busctl(
        "call",
        "org.freedesktop.NetworkManager",
        device_path,
        "org.freedesktop.NetworkManager.Device",
        "Disconnect",
    )
    if rc != 0:
        return _write_failed_response()
    return {"ok": True}


@router.post("/api/hardware/wifi/connect", dependencies=[Depends(require_admin)])
async def wifi_connect(body: WifiConnectBody):
    ssid_bytes = body.ssid.encode("utf-8")
    if not (1 <= len(ssid_bytes) <= 32):
        return JSONResponse({"error": "invalid_ssid"}, status_code=422)

    if body.psk is not None:
        psk = body.psk
        if not ((8 <= len(psk) <= 63) or (len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk))):
            return JSONResponse({"error": "invalid_psk"}, status_code=422)

    rc, _ = await busctl(
        "get-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
    )
    if rc != 0:
        return _nm_unavailable_response()

    devices = await _wifi_devices()
    if devices is None:
        return _nm_unavailable_response()
    device_path = None
    chosen_iface = None
    if body.iface:
        for path, iface in devices:
            if iface == body.iface:
                device_path = path
                chosen_iface = iface
                break
    else:
        if devices:
            device_path = devices[0][0]
            chosen_iface = devices[0][1]

    if device_path is None:
        return JSONResponse({"error": "no_wifi_device"}, status_code=404)

    settings: dict = {
        "connection": {
            "type": ("s", "802-11-wireless"),
            "id": ("s", body.ssid),
        },
        "802-11-wireless": {
            "ssid": ("ay", ssid_bytes),
            "mode": ("s", "infrastructure"),
        },
    }
    if body.psk is not None:
        settings["802-11-wireless-security"] = {
            "key-mgmt": ("s", "wpa-psk"),
            "psk": ("s", body.psk),
        }

    result = await _nm_add_and_activate(settings, device_path, chosen_iface)
    if result is None:
        return {"ok": True}
    if result.endswith(".PermissionDenied") or result == "org.freedesktop.DBus.Error.AccessDenied":
        return JSONResponse(
            {
                "error": "not_permitted",
                "hint": "Wi-Fi changes need local mode enabled (polkit rule 50-taos-hardware.rules)",
            },
            status_code=403,
        )
    if result == "dbus_fast_missing":
        return JSONResponse({"error": "dbus_fast_missing"}, status_code=503)
    return JSONResponse({"error": "connect_failed", "dbus_error": result}, status_code=502)


@router.post("/api/hardware/wifi/scan", dependencies=[Depends(require_admin)])
async def wifi_scan(body: WifiScanBody):
    rc, _ = await busctl(
        "get-property",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
        "WirelessEnabled",
    )
    if rc != 0:
        return _nm_unavailable_response()

    devices = await _wifi_devices()
    if devices is None:
        return _nm_unavailable_response()
    target_devices: list[tuple[str, str]] = []
    if body.iface is not None:
        for path, iface in devices:
            if iface == body.iface:
                target_devices.append((path, iface))
                break
        if not target_devices:
            return JSONResponse({"error": "unknown_iface"}, status_code=404)
    else:
        target_devices = devices

    scanning_ifaces: list[str] = []
    for device_path, iface in target_devices:
        rc, _ = await busctl(
            "call",
            "org.freedesktop.NetworkManager",
            device_path,
            "org.freedesktop.NetworkManager.Device.Wireless",
            "RequestScan",
            "a{sv}",
            "0",
        )
        if rc != 0:
            return _scan_failed_response()
        scanning_ifaces.append(iface)

    return JSONResponse({"ok": True, "scanning": scanning_ifaces}, status_code=202)


@router.post("/api/hardware/wifi/forget", dependencies=[Depends(require_admin)])
async def wifi_forget(body: WifiForgetBody):
    ssid_bytes = body.ssid.encode("utf-8")
    if not (1 <= len(ssid_bytes) <= 32):
        return JSONResponse({"error": "invalid_ssid"}, status_code=422)

    rc, connections_data = await busctl(
        "call",
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager/Settings",
        "org.freedesktop.NetworkManager.Settings",
        "ListConnections",
    )
    if rc != 0 or connections_data is None:
        return _write_failed_response()

    conn_list = _get_data(connections_data)
    if isinstance(conn_list, list) and len(conn_list) > 0 and isinstance(conn_list[0], list):
        paths = [p for p in conn_list[0] if isinstance(p, str)]
    elif isinstance(conn_list, list):
        paths = [p for p in conn_list if isinstance(p, str)]
    else:
        paths = []

    removed = 0
    any_read_failed = False
    for path in paths:
        if not isinstance(path, str):
            continue
        rc_gs, gs_data = await busctl(
            "call",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Settings.Connection",
            "GetSettings",
        )
        if rc_gs != 0 or gs_data is None:
            any_read_failed = True
            continue
        settings = _get_data(gs_data)
        if isinstance(settings, list) and len(settings) > 0 and isinstance(settings[0], dict):
            settings = settings[0]
        if not isinstance(settings, dict):
            continue

        conn_section = settings.get("connection")
        conn_type = conn_section.get("type") if isinstance(conn_section, dict) else None
        conn_type = _unwrap_variant(conn_type)
        if conn_type != "802-11-wireless":
            continue

        wireless_section = settings.get("802-11-wireless")
        ssid_variant = wireless_section.get("ssid") if isinstance(wireless_section, dict) else None
        ssid_val = _unwrap_variant(ssid_variant)
        if not isinstance(ssid_val, (bytes, bytearray, list)):
            continue
        if isinstance(ssid_val, list):
            try:
                ssid_val = bytes([int(b) for b in ssid_val])
            except Exception:
                continue
        if bytes(ssid_val) != ssid_bytes:
            continue

        rc_del, _ = await busctl(
            "call",
            "org.freedesktop.NetworkManager",
            path,
            "org.freedesktop.NetworkManager.Settings.Connection",
            "Delete",
        )
        if rc_del != 0:
            return _write_failed_response()
        removed += 1

    if removed == 0:
        if any_read_failed:
            return _write_failed_response()
        return JSONResponse({"error": "unknown_ssid"}, status_code=404)

    return {"ok": True, "removed": removed}


async def _nm_add_and_activate(settings: dict, device_path: str, iface: str | None = None) -> str | None:
    try:
        from dbus_fast import BusType, Message, MessageType, Variant
        from dbus_fast.aio import MessageBus
    except Exception:
        return "dbus_fast_missing"

    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        body_settings = {}
        for section, props in settings.items():
            section_dict = {}
            for key, (sig, val) in props.items():
                section_dict[key] = Variant(sig, val)
            body_settings[section] = section_dict

        # Reuse an existing profile for this SSID instead of creating a new one
        # on every connect. GetSettings is read-only; the follow-up Update is
        # what carries the PSK.
        existing = await _nm_find_existing_connection(bus, body_settings, Message, MessageType, iface)
        if existing is not None:
            existing_path, existing_settings = existing
            if body_settings.get("802-11-wireless-security") is not None:
                merged_settings = dict(existing_settings)
                merged_settings["802-11-wireless-security"] = body_settings["802-11-wireless-security"]
                update_reply = await bus.call(
                    Message(
                        destination="org.freedesktop.NetworkManager",
                        path=existing_path,
                        interface="org.freedesktop.NetworkManager.Settings.Connection",
                        member="Update",
                        signature="a{sa{sv}}",
                        body=[merged_settings],
                    )
                )
                if update_reply.message_type == MessageType.ERROR:
                    return update_reply.error_name
            activate_reply = await bus.call(
                Message(
                    destination="org.freedesktop.NetworkManager",
                    path="/org/freedesktop/NetworkManager",
                    interface="org.freedesktop.NetworkManager",
                    member="ActivateConnection",
                    signature="ooo",
                    body=[existing_path, device_path, "/"],
                )
            )
            if activate_reply.message_type == MessageType.ERROR:
                return activate_reply.error_name
            return None

        reply = await bus.call(
            Message(
                destination="org.freedesktop.NetworkManager",
                path="/org/freedesktop/NetworkManager",
                interface="org.freedesktop.NetworkManager",
                member="AddAndActivateConnection",
                signature="a{sa{sv}}oo",
                body=[body_settings, device_path, "/"],
            )
        )
        if reply.message_type == MessageType.ERROR:
            return reply.error_name
        return None
    except Exception:
        return "dbus_call_failed"
    finally:
        if bus is not None:
            bus.disconnect()


async def _nm_find_existing_connection(bus, settings: dict, Message, MessageType, iface: str | None = None) -> tuple[str, dict] | None:
    """Return a tuple (path, settings) of an existing 802-11-wireless connection
    whose SSID bytes match the requested settings, or None."""
    reply = await bus.call(
        Message(
            destination="org.freedesktop.NetworkManager",
            path="/org/freedesktop/NetworkManager/Settings",
            interface="org.freedesktop.NetworkManager.Settings",
            member="ListConnections",
            signature="",
            body=[],
        )
    )
    if reply.message_type == MessageType.ERROR:
        return None
    paths = reply.body[0] if reply.body else []
    if isinstance(paths, list) and len(paths) > 0 and isinstance(paths[0], list):
        paths = paths[0]
    if not isinstance(paths, list):
        return None

    wanted_ssid = None
    wireless = settings.get("802-11-wireless")
    if isinstance(wireless, dict):
        ssid_variant = wireless.get("ssid")
        if ssid_variant is not None:
            wanted_ssid = ssid_variant.value
    if not isinstance(wanted_ssid, (bytes, bytearray)):
        return None
    wanted_ssid = bytes(wanted_ssid)

    for path in paths:
        if not isinstance(path, str):
            continue
        settings_reply = await bus.call(
            Message(
                destination="org.freedesktop.NetworkManager",
                path=path,
                interface="org.freedesktop.NetworkManager.Settings.Connection",
                member="GetSettings",
                signature="",
                body=[],
            )
        )
        if settings_reply.message_type == MessageType.ERROR:
            continue
        sections = settings_reply.body[0] if settings_reply.body else {}
        if isinstance(sections, list) and len(sections) > 0 and isinstance(sections[0], dict):
            sections = sections[0]
        if not isinstance(sections, dict):
            continue
        conn = sections.get("connection")
        conn_type = _nm_variant_value(conn, "type") if isinstance(conn, dict) else None
        if conn_type != "802-11-wireless":
            continue
        wireless_section = sections.get("802-11-wireless")
        ssid = _nm_variant_value(wireless_section, "ssid") if isinstance(wireless_section, dict) else None
        if isinstance(ssid, (bytes, bytearray)) and bytes(ssid) == wanted_ssid:
            mode = _nm_variant_value(wireless_section, "mode") if isinstance(wireless_section, dict) else None
            if mode is not None and mode != "infrastructure":
                continue
            iface_name = _nm_variant_value(conn, "interface-name") if isinstance(conn, dict) else None
            if isinstance(iface_name, str) and iface_name and iface is not None and iface_name != iface:
                continue
            return (path, sections)
    return None


def _nm_variant_value(section: dict, key: str) -> object:
    variant = section.get(key)
    if variant is None:
        return None
    return getattr(variant, "value", variant)
