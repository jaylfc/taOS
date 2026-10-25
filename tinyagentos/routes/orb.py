"""HTTP routes for taOS Orb status (S2) -- the controller side of
docs/taosusb-plan.md's S2 section.

Admin-gated exactly like ``/api/cluster/ble/scan`` (routes/cluster_ble.py):
a session cookie for an admin user, checked with the same ``_require_admin``
helper -- not the broader ``require_admin`` that also accepts the host
local token, since these routes reveal attached hardware.

Returns plain ``port``, ``vid``, ``pid``, ``serial_number``, ``description``
fields, no signing key, no mesh_preauth.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.orb.discovery import browse_lan_orbs, list_usb_orbs, probe_agent_buddy
from tinyagentos.orb.frames import TransportUnavailable
from tinyagentos.routes.auth import _require_admin

router = APIRouter()


@router.get("/api/orb/status")
async def orb_status(request: Request):
    ok, err = _require_admin(request)
    if not ok:
        return err
    try:
        orbs = await asyncio.to_thread(list_usb_orbs)
    except TransportUnavailable:
        return JSONResponse({"error": "orb_transport_unavailable"}, status_code=503)
    return {"orbs": orbs}


@router.get("/api/orb/lan")
async def orb_lan(request: Request):
    ok, err = _require_admin(request)
    if not ok:
        return err
    results = await asyncio.gather(
        asyncio.to_thread(browse_lan_orbs),
        asyncio.to_thread(probe_agent_buddy),
        return_exceptions=True,
    )
    lan_res, buddy_res = results[0], results[1]
    lan_orbs = []
    agent_buddy = []
    errors = []
    if isinstance(lan_res, Exception):
        if isinstance(lan_res, (TransportUnavailable, OSError)):
            lan_orbs = []
            errors.append({"source": "mdns", "error": "unavailable"})
        else:
            raise lan_res
    else:
        lan_orbs = lan_res
    if isinstance(buddy_res, Exception):
        if isinstance(buddy_res, (TransportUnavailable, OSError)):
            agent_buddy = []
            errors.append({"source": "agent_buddy", "error": "unavailable"})
        else:
            raise buddy_res
    else:
        agent_buddy = buddy_res
    return {"lan_orbs": lan_orbs, "agent_buddy": agent_buddy, "errors": errors}
