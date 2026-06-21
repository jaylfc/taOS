"""taOSgo relay authorization endpoints.

The taOSgo relay calls these to decide whether an inbound off-LAN connection
should be forwarded to this taOS instance.  The relay identifies the user via
the ``X-Taos-Username`` header (set by the relay after it authenticates the
client against taos.my).

These endpoints are intentionally unauthenticated at the HTTP layer: the relay
is the trusted caller, and the relay already verified the user's taOSgo
session.  The ``X-Taos-Username`` header is only accepted when the request
arrives from a loopback or trusted-proxy source (enforced by the relay
deployment, not here).
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/relay/authorize")
async def relay_authorize(
    request: Request,
    x_taos_username: str | None = Header(None, alias="X-Taos-Username"),
):
    """Return whether the named user is allowed through the taOSgo relay.

    The relay calls this before forwarding an inbound connection.  If the
    user has the ``remote_relay_pro`` entitlement the response is
    ``{"allow": true}``; otherwise ``{"allow": false}``.
    """
    if not x_taos_username:
        return JSONResponse({"allow": False})
    auth_mgr = request.app.state.auth
    allowed = auth_mgr.check_remote_relay_pro(x_taos_username)
    return JSONResponse({"allow": allowed})


@router.get("/api/relay/tls-allow")
async def relay_tls_allow(
    request: Request,
    x_taos_username: str | None = Header(None, alias="X-Taos-Username"),
):
    """Return whether the named user's relay connection may use TLS.

    The relay uses this to decide whether to offer a TLS listener for the
    user's taOSgo session.  Only Pro-entitled users get TLS.
    """
    if not x_taos_username:
        return JSONResponse({"allow": False})
    auth_mgr = request.app.state.auth
    allowed = auth_mgr.check_remote_relay_pro(x_taos_username)
    return JSONResponse({"allow": allowed})
