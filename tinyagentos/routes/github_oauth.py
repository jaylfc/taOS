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

import asyncio
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
    """Return the shared httpx.AsyncClient from application state."""
    return request.app.state.http_client


def _identities_store(request: Request):
    """Return the GitHubIdentitiesStore from application state."""
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


# ---------------------------------------------------------------------------
# GitHub App: installation endpoints
# ---------------------------------------------------------------------------

_INSTALL_PAGE_URL = "https://github.com/apps/{app_slug}/installations/new"


def _app_config(request: Request):
    """Return the AppConfig from application state."""
    return getattr(request.app.state, "config", None)


def _app_installations_store(request: Request):
    """Return the GitHubAppInstallations store from application state."""
    return getattr(request.app.state, "github_app_installations", None)


async def _get_app_private_key(request: Request) -> str | None:
    """Return the GitHub App private key from SecretsStore, or None.

    The key is stored under the well-known name ``github-app-private-key``
    in the encrypted SecretsStore (not in plaintext config). Returns the
    inline PEM string if configured, None otherwise.
    """
    secrets = getattr(request.app.state, "secrets", None)
    if secrets is None:
        return None
    try:
        rec = await secrets.get("github-app-private-key")
    except Exception:
        return None
    if rec and rec.get("value"):
        return rec["value"]
    return None


def _app_is_configured(request: Request) -> bool:
    """Check whether the GitHub App is configured at all."""
    cfg = _app_config(request)
    return bool(cfg and cfg.github_app_id)


@router.get("/api/github/app/installations")
async def list_app_installations(request: Request):
    """List GitHub App installations with their accessible repositories.

    Requires github_app_id in config and github-app-private-key secret.
    """
    cfg = _app_config(request)
    if not _app_is_configured(request):
        return JSONResponse(
            {"error": "GitHub App not configured (set github_app_id in config and add github-app-private-key secret)"},
            status_code=501,
        )
    private_key = await _get_app_private_key(request)
    if not private_key:
        return JSONResponse(
            {"error": "GitHub App not configured (add github-app-private-key secret in Secrets page)"},
            status_code=501,
        )

    http = _http(request)
    installs_store = _app_installations_store(request)

    from tinyagentos.github_app import (
        list_installations,
        list_installation_repos_cached,
        get_installation_token,
    )

    # Fetch installations from the GitHub API using the JWT
    raw_installations = await list_installations(
        cfg.github_app_id, private_key, http
    )

    # Phase 1: record new installations locally (sequential — uses lock)
    for inst in raw_installations:
        iid = inst.get("id")
        if not iid:
            continue
        if installs_store and not installs_store.get(iid):
            account = inst.get("account", {})
            await installs_store.add(
                installation_id=iid,
                account_login=account.get("login", ""),
                account_type=account.get("type", ""),
                account_avatar_url=account.get("avatar_url", ""),
                repository_selection=inst.get("repository_selection", "selected"),
            )

    # Phase 2: mint tokens and fetch repos in parallel across installations.
    # get_installation_token has its own 5-min TTL cache (_token_cache), so
    # subsequent calls within the window reuse cached tokens — no extra POSTs.
    async def _fetch_one(inst: dict) -> tuple[int, dict, list[dict]]:
        iid: int = inst["id"]
        token = await get_installation_token(
            cfg.github_app_id, private_key, iid, http
        )
        repos: list[dict] = []
        if token:
            repos = await list_installation_repos_cached(
                iid, token, http, app_id=cfg.github_app_id, private_key=private_key
            )
        return iid, inst, repos

    valid = [inst for inst in raw_installations if inst.get("id")]
    fetched = await asyncio.gather(*(_fetch_one(inst) for inst in valid))

    # Phase 3: build result list
    result = []
    for iid, inst, repos in fetched:
        account = inst.get("account", {})
        result.append({
            "id": iid,
            "account": {
                "login": account.get("login", ""),
                "type": account.get("type", ""),
                "avatar_url": account.get("avatar_url", ""),
            },
            "repository_selection": inst.get("repository_selection", "selected"),
            "permissions": [
                f"{k}:{v}" for k, v in inst.get("permissions", {}).items()
            ],
            "repositories": [
                {
                    "full_name": r.get("full_name", ""),
                    "name": r.get("name", ""),
                    "private": r.get("private", False),
                    "description": r.get("description", ""),
                }
                for r in repos
            ],
            "created_at": inst.get("created_at", ""),
        })

    return {"installations": result}


@router.post("/api/github/app/install")
async def begin_app_installation(request: Request):
    """Redirect the user to GitHub's App installation page.

    Returns the URL the frontend should open. The user will be redirected
    back to /api/github/app/callback after installation completes.
    """
    cfg = _app_config(request)
    if not _app_is_configured(request):
        return JSONResponse(
            {"error": "GitHub App not configured (set github_app_id in config and add github-app-private-key secret)"},
            status_code=501,
        )
    private_key = await _get_app_private_key(request)
    if not private_key:
        return JSONResponse(
            {"error": "GitHub App not configured (add github-app-private-key secret in Secrets page)"},
            status_code=501,
        )

    http = _http(request)
    from tinyagentos.github_app import _get_app_slug

    app_slug = await _get_app_slug(
        cfg.github_app_id, private_key, http
    )
    if not app_slug:
        return JSONResponse(
            {"error": "Could not determine GitHub App slug from API"},
            status_code=502,
        )

    install_url = _INSTALL_PAGE_URL.format(app_slug=app_slug)
    return {"install_url": install_url}


@router.get("/api/github/app/callback")
async def app_installation_callback(
    request: Request,
    installation_id: int | None = None,
    setup_action: str = "install",
):
    """Handle the post-installation redirect from GitHub.

    GitHub redirects here after a user installs the app, with
    ?installation_id=...&setup_action=install in the query string.
    """
    if not installation_id:
        return JSONResponse(
            {"error": "Missing installation_id parameter"},
            status_code=400,
        )

    cfg = _app_config(request)
    if not _app_is_configured(request):
        return JSONResponse(
            {"error": "GitHub App not configured"},
            status_code=501,
        )
    private_key = await _get_app_private_key(request)
    if not private_key:
        return JSONResponse(
            {"error": "GitHub App not configured (add github-app-private-key secret in Secrets page)"},
            status_code=501,
        )

    http = _http(request)
    from fastapi.responses import RedirectResponse
    from tinyagentos.github_app import get_installation_token

    # Verify the installation works by minting a token
    token = await get_installation_token(
        cfg.github_app_id, private_key, installation_id, http
    )
    if not token:
        return JSONResponse(
            {"error": "Failed to verify installation"},
            status_code=502,
        )

    # Fetch installation details with JWT (the /user endpoint returns the bot
    # user, not the installing account, so we use /app/installations/{id}).
    from tinyagentos.github_app import generate_jwt
    jwt = generate_jwt(cfg.github_app_id, private_key)
    try:
        install_resp = await http.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {jwt}",
                "Accept": "application/vnd.github+json",
            },
            timeout=10,
        )
    except Exception:
        logger.warning(
            "Failed to fetch installation %s details (network/timeout), saving "
            "minimal record — the install itself succeeded",
            installation_id,
        )
        install_resp = None

    if install_resp is not None and install_resp.status_code == 200:
        inst_data = install_resp.json()
        account = inst_data.get("account", {})
        store = _app_installations_store(request)
        if store:
            await store.add(
                installation_id=installation_id,
                account_login=account.get("login", ""),
                account_type=account.get("type", ""),
                account_avatar_url=account.get("avatar_url", ""),
                repository_selection=inst_data.get("repository_selection", "selected"),
            )
        logger.info(
            "GitHub App installation %s %s (%s)",
            installation_id,
            setup_action,
            account.get("login", ""),
        )
    else:
        status_str = str(install_resp.status_code) if install_resp is not None else "exception"
        logger.warning(
            "Failed to fetch installation %s details (HTTP %s), saving minimal "
            "record — the install itself succeeded",
            installation_id, status_str,
        )
        store = _app_installations_store(request)
        if store and not store.get(installation_id):
            await store.add(
                installation_id=installation_id,
                account_login="",
                account_type="",
                account_avatar_url="",
                repository_selection="selected",
            )
        return RedirectResponse(url="/app/secrets?install_warning=1", status_code=302)

    return RedirectResponse(url="/app/secrets", status_code=302)


@router.delete("/api/github/app/installations/{installation_id}")
async def delete_app_installation(request: Request, installation_id: int):
    """Uninstall the GitHub App from the given installation."""
    cfg = _app_config(request)
    if not _app_is_configured(request):
        return JSONResponse(
            {"error": "GitHub App not configured"},
            status_code=501,
        )
    private_key = await _get_app_private_key(request)
    if not private_key:
        return JSONResponse(
            {"error": "GitHub App not configured (add github-app-private-key secret in Secrets page)"},
            status_code=501,
        )

    http = _http(request)
    from tinyagentos.github_app import delete_installation

    deleted = await delete_installation(
        cfg.github_app_id, private_key, installation_id, http
    )

    if deleted is None:
        return JSONResponse(
            {"error": "Failed to delete installation (upstream error)"},
            status_code=502,
        )

    if not deleted:
        return JSONResponse(
            {"error": "Installation not found or could not be deleted"},
            status_code=404,
        )

    store = _app_installations_store(request)
    if store:
        await store.remove(installation_id)

    return {"status": "deleted"}
