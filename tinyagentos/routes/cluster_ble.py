"""HTTP routes for taOSusb Bluetooth pairing (S1) -- the controller side of
docs/taosusb-pairing-plan.md's Design + S1 section.

Admin-gated exactly like ``/api/cluster/pairing/manual`` (routes/cluster.py):
a session cookie for an admin user, checked with the same ``_require_admin``
helper -- not the broader ``require_admin`` that also accepts the host
local token, since these routes drive a physical radio on an admin's say-so.

No response built here may ever carry ``node_key``, a signing key, an
``llm`` key or ``mesh_preauth``: ``BlePairingManager`` (cluster/ble/pairing.py)
only ever returns plain ``name``/``kind``/``board_id``/``code``/``session``
fields, and this module passes those straight through without touching the
sealed provision payload itself.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.cluster.ble.pairing import BluetoothError, PairError
from tinyagentos.routes.auth import _require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

_SCAN_SECONDS_MIN = 2
_SCAN_SECONDS_MAX = 15


class PairStartBody(BaseModel):
    address: str


class PairSessionBody(BaseModel):
    session: str


def _manager(request: Request):
    return getattr(request.app.state, "ble_pairing", None)


def _clamp_seconds(seconds: float) -> float:
    return max(_SCAN_SECONDS_MIN, min(_SCAN_SECONDS_MAX, seconds))


@router.get("/api/cluster/ble/scan")
async def ble_scan(request: Request, seconds: float = 6):
    ok, err = _require_admin(request)
    if not ok:
        return err
    manager = _manager(request)
    if manager is None:
        return JSONResponse({"error": "bluetooth_unavailable"}, status_code=503)
    try:
        devices = await manager.scan(_clamp_seconds(seconds))
    except BluetoothError:
        return JSONResponse({"error": "bluetooth_unavailable"}, status_code=503)
    return {"devices": devices}


@router.post("/api/cluster/ble/pair/start")
async def ble_pair_start(request: Request, body: PairStartBody):
    ok, err = _require_admin(request)
    if not ok:
        return err
    address = body.address.strip()
    if not address:
        return JSONResponse({"error": "address is required"}, status_code=400)
    manager = _manager(request)
    if manager is None:
        return JSONResponse({"error": "bluetooth_unavailable"}, status_code=503)
    try:
        result = await manager.start(address)
    except BluetoothError:
        return JSONResponse({"error": "bluetooth_unavailable"}, status_code=503)
    except PairError as exc:
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    return result


@router.post("/api/cluster/ble/pair/confirm")
async def ble_pair_confirm(request: Request, body: PairSessionBody):
    ok, err = _require_admin(request)
    if not ok:
        return err
    session_id = body.session.strip()
    if not session_id:
        return JSONResponse({"error": "session is required"}, status_code=400)
    manager = _manager(request)
    if manager is None:
        return JSONResponse({"error": "bluetooth_unavailable"}, status_code=503)
    try:
        node = await manager.confirm(session_id)
    except BluetoothError:
        return JSONResponse({"error": "bluetooth_unavailable"}, status_code=503)
    except PairError as exc:
        if exc.status == 502:
            return JSONResponse({"error": "board_rejected", "why": exc.why}, status_code=502)
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    return {"node": node}


@router.post("/api/cluster/ble/pair/cancel")
async def ble_pair_cancel(request: Request, body: PairSessionBody):
    ok, err = _require_admin(request)
    if not ok:
        return err
    manager = _manager(request)
    session_id = body.session.strip()
    if manager is not None and session_id:
        await manager.cancel(session_id)
    return Response(status_code=204)
