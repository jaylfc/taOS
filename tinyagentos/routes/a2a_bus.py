# tinyagentos/routes/a2a_bus.py
"""Read-only proxy for the external taOSmd A2A coordination bus.

The taOSmd coordination bus (``taosmd serve``) is a SEPARATE service co-located
with the controller (default http://127.0.0.1:7900). It is where cross-product
agents (@taOS, @taOSmd, @hermes) coordinate. This is DISTINCT from taOS's own
internal per-project a2a channels (tinyagentos/projects/a2a.py).

These endpoints are a thin READ-ONLY proxy: the Messages app can list bus
channels and read messages, but there is no send/post path here. The bus is
unauthenticated on the LAN; the URL is resolved from ``TAOS_A2A_BUS_URL``.

Bus API (verified live):
  GET {bus}/a2a/channels
      -> {"channels":[{"channel","members","message_count","created_ts","last_ts"}, ...]}
  GET {bus}/a2a/messages?thread={channel}&limit={n}
      -> {"messages":[{"id","ts","from","body","thread","reply_to"}, ...]}
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from tinyagentos.agent_token_auth import check_agent_scope

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_BUS_URL = "http://127.0.0.1:7900"


def _bus_url() -> str:
    """Resolve the bus base URL from the environment, trailing slash stripped."""
    return os.environ.get("TAOS_A2A_BUS_URL", _DEFAULT_BUS_URL).rstrip("/")


async def _authorize_bus_read(request: Request) -> None:
    """Gate a bus-read request.

    Admin (session cookie or local token) is allowed unconditionally -- the
    middleware has already set request.state.is_admin for those.  Otherwise the
    caller must present a registry JWT holding an active ``a2a_receive`` grant;
    check_agent_scope raises 401 (bad/malformed token) or 403 (valid token but
    not active / missing scope) and returns None only when no Bearer header is
    present, which is rejected here as 403 (fail closed).
    """
    if getattr(request.state, "is_admin", False):
        return
    caller = await check_agent_scope(request, "a2a_receive")
    if caller is None:
        raise HTTPException(status_code=403, detail="forbidden")


@router.get("/api/a2a/bus/channels")
async def bus_channels(request: Request):
    """List coordination-bus channels, sorted by last activity (newest first).

    Authorized readers: an admin session, the host local token, or an active
    agent registry JWT holding the ``a2a_receive`` scope.

    On any bus error (timeout / connection refused / non-200) this returns an
    empty list with ``available: false`` and HTTP 200, so the frontend can show
    a clean offline state rather than crashing.
    """
    await _authorize_bus_read(request)
    bus = _bus_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{bus}/a2a/channels")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 (degrade to an offline state)
        logger.warning("A2A bus channels fetch failed (%s): %s", bus, exc)
        return JSONResponse({"channels": [], "available": False}, status_code=200)

    channels = data.get("channels", []) if isinstance(data, dict) else []
    channels = sorted(
        channels,
        key=lambda c: c.get("last_ts", 0) or 0,
        reverse=True,
    )
    return {"channels": channels, "available": True}


@router.get("/api/a2a/bus/messages")
async def bus_messages(request: Request, channel: str = "", limit: int = 100):
    """Read messages from one bus channel, oldest-first as the bus returns them.

    Authorized readers: an admin session, the host local token, or an active
    agent registry JWT holding the ``a2a_receive`` scope.

    ``channel`` is required and maps to the bus ``thread`` query param. ``limit``
    is clamped to 1..500. On a bus error this returns an empty list with
    ``available: false`` and HTTP 200.
    """
    await _authorize_bus_read(request)
    if not channel:
        return JSONResponse({"error": "channel required"}, status_code=400)

    limit = max(1, min(500, limit))
    bus = _bus_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{bus}/a2a/messages",
                params={"thread": channel, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 (degrade to an offline state)
        logger.warning("A2A bus messages fetch failed (%s): %s", bus, exc)
        return JSONResponse({"messages": [], "available": False}, status_code=200)

    messages = data.get("messages", []) if isinstance(data, dict) else []
    return {"messages": messages, "available": True}
