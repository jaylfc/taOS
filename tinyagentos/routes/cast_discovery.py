"""Cast discovery health: is the bundled mDNS reflector actually bridging?

Session-gated like every other /api route (not on any agent scope). The
cast device list (C1) uses this, through
``services.mdns_reflector.discovery_view``, to show "discovery is not
working" instead of an empty list that reads as "no devices found".
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from tinyagentos.services.mdns_reflector import (
    disabled_status,
    start_failed_status,
)

router = APIRouter()


@router.get("/api/cast/discovery/status")
async def cast_discovery_status(request: Request) -> dict:
    """``state`` is ``ok``, ``unavailable`` (with ``reason``) or ``disabled``."""
    reflector = getattr(request.app.state, "mdns_reflector", None)
    if reflector is None:
        error = getattr(request.app.state, "mdns_reflector_start_error", None)
        if error:
            return start_failed_status(error)
        return disabled_status()
    return reflector.status()
