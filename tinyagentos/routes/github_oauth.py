"""API routes for the GitHub OAuth Device Flow ("Connect GitHub").

taOS instances have no fixed callback URL, so we use GitHub's OAuth Device
Flow (RFC 8628), which needs only the public Client ID — no client secret.

Routes (all under /api/github/):
- POST /oauth/device/start  -> begin the flow, return the user_code + URLs
- POST /oauth/device/poll   -> poll once for the token; store identity on success
- GET  /identities          -> list connected identities (NO tokens)
- DELETE /identities/{id}    -> remove an identity

SECURITY: tokens are encrypted at rest (Fernet, shared secrets key) and are
NEVER logged or returned by any endpoint.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.github_oauth import (
    ACCESS_TOKEN_URL,
    DEVICE_CODE_URL,
    DEVICE_FLOW_SCOPE,
    DEVICE_GRANT_TYPE,
    USER_URL,
    client_id,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_TIMEOUT = 10.0
_JSON = {"Accept": "application/json"}

# Token-endpoint errors that mean "keep waiting" vs. "give up".
_PENDING_ERRORS = {"authorization_pending", "slow_down"}
_TERMINAL_ERRORS = {"expired_token", "access_denied", "unsupported_grant_type"}


class DevicePollBody(BaseModel):
    device_code: str


def _http(request: Request):
    return request.app.state.http_client


def _identities_store(request: Request):
    return getattr(request.app.state, "github_identities", None)


# ---------------------------------------------------------------------------
# Device flow: start
# ---------------------------------------------------------------------------

@router.post("/api/github/oauth/device/start")
async def device_start(request: Request):
    """Begin the device flow. Returns user_code, verification_uri, device_code."""
    http = _http(request)
    try:
        resp = await http.post(
            DEVICE_CODE_URL,
            data={"client_id": client_id(), "scope": DEVICE_FLOW_SCOPE},
            headers=_JSON,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.exception("github device/start failed: %s", exc)
        return JSONResponse(
            {"error": "Failed to start GitHub device flow"}, status_code=502
        )

    if "device_code" not in data or "user_code" not in data:
        # GitHub returns {error: ...} on bad client_id etc. Never echo secrets.
        logger.warning("github device/start unexpected response: %s", data.get("error"))
        return JSONResponse(
            {"error": data.get("error_description") or "GitHub did not return a device code"},
            status_code=502,
        )

    # device_code is returned to the client so it can poll; this is standard
    # per the protocol and is not a long-lived credential.
    return {
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri", "https://github.com/login/device"),
        "device_code": data["device_code"],
        "interval": data.get("interval", 5),
        "expires_in": data.get("expires_in", 900),
    }


# ---------------------------------------------------------------------------
# Device flow: poll (single poll per call; frontend drives the loop)
# ---------------------------------------------------------------------------

@router.post("/api/github/oauth/device/poll")
async def device_poll(request: Request, body: DevicePollBody):
    """Poll the token endpoint once for *device_code*.

    - access_token -> fetch the user, store the identity, status="connected"
    - authorization_pending / slow_down -> status="pending"
    - expired_token / access_denied -> status="error"
    """
    http = _http(request)
    try:
        resp = await http.post(
            ACCESS_TOKEN_URL,
            data={
                "client_id": client_id(),
                "device_code": body.device_code,
                "grant_type": DEVICE_GRANT_TYPE,
            },
            headers=_JSON,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.exception("github device/poll failed: %s", exc)
        return JSONResponse(
            {"status": "error", "error": "poll_failed"}, status_code=502
        )

    access_token = data.get("access_token")
    if access_token:
        scopes = data.get("scope", "")
        try:
            user_resp = await http.get(
                USER_URL,
                headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
                timeout=_TIMEOUT,
            )
            user_resp.raise_for_status()
            user = user_resp.json()
        except Exception as exc:
            # Do NOT log the token. Only log that the user lookup failed.
            logger.exception("github user lookup after device flow failed: %s", exc)
            return JSONResponse(
                {"status": "error", "error": "user_lookup_failed"}, status_code=502
            )

        store = _identities_store(request)
        if store is None:
            logger.error("github_identities store not configured")
            return JSONResponse(
                {"status": "error", "error": "store_unavailable"}, status_code=500
            )

        identity = await store.add(
            login=user.get("login", ""),
            avatar_url=user.get("avatar_url", ""),
            token=access_token,
            scopes=scopes,
        )
        return {"status": "connected", "identity": identity}

    error = data.get("error", "")
    if error == "slow_down":
        # RFC 8628 §3.5: the client must back off. Signal the frontend to add
        # to its poll interval.
        return {"status": "pending", "slow_down": True}
    if error in _PENDING_ERRORS:
        return {"status": "pending"}
    if error in _TERMINAL_ERRORS:
        return {"status": "error", "error": error}
    # Unknown error shape — surface generically without leaking detail.
    return {"status": "error", "error": error or "unknown"}


# ---------------------------------------------------------------------------
# Identities: list / delete (NO tokens ever returned)
# ---------------------------------------------------------------------------

@router.get("/api/github/identities")
async def list_identities(request: Request):
    store = _identities_store(request)
    if store is None:
        return []
    return await store.list()


@router.delete("/api/github/identities/{identity_id}")
async def delete_identity(request: Request, identity_id: str):
    # Validate the path param is a UUID before touching the store.
    try:
        uuid.UUID(identity_id)
    except ValueError:
        return JSONResponse({"error": "Invalid identity id"}, status_code=400)

    store = _identities_store(request)
    if store is None:
        return JSONResponse({"error": "Store unavailable"}, status_code=500)
    deleted = await store.delete(identity_id)
    if not deleted:
        return JSONResponse({"error": "Identity not found"}, status_code=404)
    return {"status": "deleted"}
