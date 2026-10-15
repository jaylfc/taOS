"""Screencast C3: cast sessions (session-authenticated) and cast media (token-only).

Two halves with two different gates:

* ``/api/cast/sessions``: start, refresh and end a cast. Ordinary taOS routes
  behind the session gate; only the signed-in owner of the view can mint.
* ``/cast/media/{kind}/{view_id}/...``: what the TV fetches. A TV carries no
  session, so this prefix is exempt from AuthMiddleware (GET/HEAD only) and is
  gated SOLELY by the cast token in ``?token=``. Unknown, expired or revoked
  token -> 401; a live token for a different view -> 403; a token bound to a
  device address fetched from elsewhere -> 403.

Senders (Chromecast default media receiver, AirPlay ``play_url``) and the
paired-device registry are later slices; the registry's unpair calls
``app.state.cast_tokens.revoke_for_device(device_id)``.
"""
from __future__ import annotations

import logging
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.cast_render import (
    CAST_VIEW_KINDS,
    STILL_REFRESH_SECONDS,
    parse_view,
    render_still_png,
    view_exists,
)

logger = logging.getLogger(__name__)
router = APIRouter()

CAST_MEDIA_PREFIX = "/cast/media/"

# The TV must not cache a frame past revocation, and the tokenised URL must not
# leak onward via Referer.
_MEDIA_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class CastTokenRedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.args, tuple) and len(record.args) >= 3:
                path_str = str(record.args[2])
                if path_str.startswith(CAST_MEDIA_PREFIX):
                    parts = urlsplit(path_str)
                    if parts.query:
                        q = parse_qsl(parts.query, keep_blank_values=True)
                        q = [(k, "REDACTED") if k == "token" else (k, v) for k, v in q]
                        new_query = urlencode(q, doseq=True)
                        new_path = urlunsplit(
                            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
                        )
                        record.args = (
                            record.args[0],
                            record.args[1],
                            new_path,
                            *record.args[3:],
                        )
        except Exception:
            pass
        return True


_uvicorn_access_logger = logging.getLogger("uvicorn.access")
if not any(isinstance(f, CastTokenRedactFilter) for f in _uvicorn_access_logger.filters):
    _uvicorn_access_logger.addFilter(CastTokenRedactFilter())


class CastStartIn(BaseModel):
    view: str = Field(..., max_length=256)
    device_id: str = Field(..., min_length=1, max_length=256)
    mode: Literal["still", "live"] = "still"
    # Optional LAN address of the target device. When given, the media route
    # serves only requests from that address (device binding where the
    # transport allows it).
    device_address: str | None = Field(default=None, max_length=64)
def _grant_view(grant) -> dict:
    return {
        "cast_id": grant.cast_id,
        "view": grant.view,
        "device_id": grant.device_id,
        "mode": grant.mode,
        "expires_at": grant.expires_at,
    }


@router.post("/api/cast/sessions", status_code=201)
async def start_cast(
    body: CastStartIn, request: Request, user: CurrentUser = Depends(current_user),
):
    """Start a cast of ONE view to ONE device; return a short-lived cast token.

    The token is shown exactly once. Refresh it while the cast is active with
    POST /api/cast/sessions/{cast_id}/refresh; end it with DELETE.
    """
    parsed = parse_view(body.view)
    if parsed is None or parsed[0] not in CAST_VIEW_KINDS:
        return JSONResponse(
            {"error": f"unsupported view; castable kinds: {', '.join(CAST_VIEW_KINDS)}"},
            status_code=400,
        )
    if body.mode == "live":
        # HLS encode is a follow-up: it needs measured encode cost on the
        # RK3588 and Pi 5 before a live cast is promised.
        return JSONResponse(
            {"error": "live cast encode is not available yet; use mode=still"},
            status_code=501,
        )
    view_kind, view_id = parsed
    record = await view_exists(request.app, view_kind, view_id)
    # Existence-hiding: a view the caller does not own reads as not found.
    if record is None or (
        not user.is_admin and record.get("user_id") != user.user_id
    ):
        return JSONResponse({"error": "view not found"}, status_code=404)
    store = request.app.state.cast_tokens
    token, grant = store.mint(
        user_id=user.user_id,
        view_kind=view_kind,
        view_id=view_id,
        device_id=body.device_id,
        device_address=body.device_address,
        mode=body.mode,
    )
    shape = "still.png"
    out = _grant_view(grant)
    out.update(
        token=token,
        media_url=f"{CAST_MEDIA_PREFIX}{view_kind}/{view_id}/{shape}?token={token}",
        ttl_seconds=store.ttl_seconds,
        refresh_interval_seconds=STILL_REFRESH_SECONDS,
    )
    return out


def _owned_grant(request: Request, cast_id: str, user: CurrentUser):
    grant = request.app.state.cast_tokens.get(cast_id)
    if grant is None or (not user.is_admin and grant.user_id != user.user_id):
        return None
    return grant


@router.post("/api/cast/sessions/{cast_id}/refresh")
async def refresh_cast(
    cast_id: str, request: Request, user: CurrentUser = Depends(current_user),
):
    """Extend an active cast by one TTL. An expired or ended cast stays dead."""
    if _owned_grant(request, cast_id, user) is None:
        return JSONResponse({"error": "cast not found"}, status_code=404)
    grant = request.app.state.cast_tokens.refresh(cast_id)
    if grant is None:
        return JSONResponse(
            {"error": "cast is no longer active; start a new cast"}, status_code=409,
        )
    return _grant_view(grant)


@router.delete("/api/cast/sessions/{cast_id}")
async def end_cast(
    cast_id: str, request: Request, user: CurrentUser = Depends(current_user),
):
    """End a cast: its token stops working immediately."""
    if _owned_grant(request, cast_id, user) is None:
        return JSONResponse({"error": "cast not found"}, status_code=404)
    request.app.state.cast_tokens.revoke(cast_id)
    return {"ended": True, "cast_id": cast_id}


def _authorize_media(request: Request, view_kind: str, view_id: str):
    """Resolve the cast token for a media fetch. Returns the grant or a response."""
    grant = request.app.state.cast_tokens.resolve(request.query_params.get("token"))
    if grant is None:
        return JSONResponse({"error": "invalid or expired cast token"}, status_code=401)
    if grant.view_kind != view_kind or grant.view_id != view_id:
        return JSONResponse({"error": "cast token is not valid for this view"}, status_code=403)
    if grant.device_address:
        peer = request.client.host if request.client else None
        if peer != grant.device_address:
            return JSONResponse(
                {"error": "cast token is bound to another device"}, status_code=403,
            )
    return grant


@router.api_route(
    CAST_MEDIA_PREFIX + "{view_kind}/{view_id}/still.png", methods=["GET", "HEAD"],
)
async def cast_media_still(view_kind: str, view_id: str, request: Request):
    """The TV-facing still image of one view. Gated solely by the cast token."""
    grant = _authorize_media(request, view_kind, view_id)
    if isinstance(grant, JSONResponse):
        return grant
    if await view_exists(request.app, view_kind, view_id) is None:
        return JSONResponse({"error": "view not found"}, status_code=404)
    png = await render_still_png(request.app, view_kind, view_id)
    return Response(content=png, media_type="image/png", headers=_MEDIA_HEADERS)


@router.api_route(
    CAST_MEDIA_PREFIX + "{view_kind}/{view_id}/live.m3u8", methods=["GET", "HEAD"],
)
async def cast_media_live(view_kind: str, view_id: str, request: Request):
    """Live HLS of one view. Token-gated; the encoder is not built yet (501)."""
    grant = _authorize_media(request, view_kind, view_id)
    if isinstance(grant, JSONResponse):
        return grant
    return JSONResponse(
        {"error": "live cast encode is not available yet; use the still image"},
        status_code=501,
        headers=_MEDIA_HEADERS,
    )
