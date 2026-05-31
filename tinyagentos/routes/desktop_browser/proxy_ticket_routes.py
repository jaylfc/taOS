"""MAIN-app endpoint that mints a browser-proxy redeem ticket.

Lives on the taOS shell origin (:6969) behind the normal session gate.
The frontend calls this when opening the browser app, then points the
proxied iframe at ``<proxy-origin>/__taos/redeem?ticket=...&next=...`` to
establish the ``taos_browser`` cookie on the separate proxy origin.

See :mod:`tinyagentos.browser_proxy_origin` for the redeem side and
:mod:`tinyagentos.routes.desktop_browser.proxy_ticket` for the token
format.
"""
from __future__ import annotations

import os
import time
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router
from tinyagentos.routes.desktop_browser.proxy_ticket import mint_proxy_ticket


def _signing_key(request: Request) -> bytes:
    """Return the shared browser-proxy signing key, creating it on first use.

    A per-process random 32-byte secret stored on ``app.state`` and shared
    with the proxy-origin app (which references the same state object).
    Regenerated on restart — outstanding tickets/cookies simply stop
    validating and the frontend transparently re-redeems.
    """
    key = getattr(request.app.state, "browser_proxy_signing_key", None)
    if not key:
        key = os.urandom(32)
        request.app.state.browser_proxy_signing_key = key
    return key


@router.api_route("/api/desktop/browser/proxy-ticket", methods=["GET", "POST"])
async def proxy_ticket(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Mint a short-lived signed ticket for the proxy origin's redeem flow.

    Requires the normal taOS session (enforced by the auth middleware and
    the ``get_current_user`` dependency). Returns the opaque token plus
    its TTL so the caller can schedule a refresh.
    """
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    signing_key = _signing_key(request)
    _ticket, token = mint_proxy_ticket(user_id, signing_key=signing_key)
    return JSONResponse(
        {"ticket": token, "expires_in": max(0, _ticket.exp - int(time.time()))}
    )
