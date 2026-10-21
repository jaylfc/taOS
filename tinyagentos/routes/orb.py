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

from tinyagentos.orb.discovery import list_usb_orbs
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