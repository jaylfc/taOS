"""taOSgo Phase 2 -- app-join endpoint.

POST /api/taosgo/app-join mints a Headscale preauth key for a mesh join.

Authentication is provided by the global auth gate (``AuthMiddleware``): a
session cookie (browser flow, with CSRF) or a host local-token Bearer
(programmatic clients). The handler re-checks for a session or a valid Bearer
and 401s itself if neither is present, so a future allowlist drift cannot arm
the route for anonymous callers (defence in depth).

The password-only bypass from PR 130 was unreachable (the auth gate answers
first for unauthenticated callers) and has been removed. Once the 2FA login
split (replacement card tsk-m7ufkp) lands, app-join must be included in the
2FA-required set (challenge or app-password flow for clients).
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from tinyagentos.middleware.csrf import verify_csrf

logger = logging.getLogger(__name__)

router = APIRouter()

# App-password / host local-token scheme. auto_error=False so a request that
# carries no Authorization header yields credentials=None instead of a
# framework 403 -- the handler must decide the response so it can return 401.
security = HTTPBearer(auto_error=False)


class AppJoinResponse(BaseModel):
    preauth_key: str
    hostname: str


@router.post("/api/taosgo/app-join", dependencies=[Depends(verify_csrf)])
async def app_join(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    """Create a Headscale preauth key for mesh join.

    Authentication:
    1. Session cookie + CSRF token (browser flow).
    2. App-password / host local-token Bearer (programmatic clients).

    A caller with neither credential is rejected with 401 by the handler
    itself. The auth gate already does this for live traffic; the handler-level
    check is defence in depth against an allowlist drift arming the route for
    anonymous callers (tsk-wswj26).
    """
    auth_mgr = getattr(request.app.state, "auth", None)
    if auth_mgr is None:
        raise HTTPException(
            status_code=503,
            detail="Auth manager not available",
        )

    current_user = None
    auth_via = None

    # Method 1: Session cookie (browser flow)
    session_token = request.cookies.get("taos_session")
    if session_token:
        user_id = auth_mgr.validate_session(session_token)
        if user_id:
            current_user = auth_mgr.get_user_by_id(user_id)
            auth_via = "session_cookie"

    # Method 2: App-password / host local-token Bearer (programmatic clients)
    if not current_user and credentials:
        app_password = credentials.credentials
        if app_password and auth_mgr.validate_local_token(app_password):
            # Local tokens map to the primary/admin user.
            current_user = auth_mgr.get_primary_user()
            auth_via = "app_password_bearer"
        else:
            raise HTTPException(
                status_code=401,
                detail="Invalid app password",
            )

    # Defence in depth: reject any caller with neither a session nor a valid
    # Bearer token. The auth gate (AuthMiddleware) already answers 401 here for
    # live traffic; this guards against a future allowlist drift arming the
    # route for anonymous callers (tsk-wswj26).
    if not current_user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )

    # Generate a Headscale preauth key (placeholder for now).
    # In production, this would call the Headscale admin API.
    preauth_key = f"preauth_{current_user.get('id', 'unknown')}_{Path(__file__).stat().st_mtime}"

    # Get hostname (would come from request or config)
    hostname = "taos-device"

    logger.info(
        "App-join successful via %s for user %s",
        auth_via,
        current_user.get("username", "unknown"),
    )

    return AppJoinResponse(
        preauth_key=preauth_key,
        hostname=hostname,
    )
