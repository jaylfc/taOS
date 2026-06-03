"""The browser-proxy origin — a SECOND, deliberately API-free ASGI app.

Frontend win #2 background
==========================
A service worker can only intercept fetches from clients of its OWN
origin. The proxied iframe is currently same-origin with the taOS shell
but sandboxed to an OPAQUE origin (no ``allow-same-origin``), so the
shell-origin SW (``/__taos/sw.js``) never controls it.

The fix is to give the iframe a REAL, separate origin with NO taOS APIs
on it, so ``allow-same-origin`` is safe there: even if the proxied page's
JS escaped the sandbox it would only reach an origin that exposes the
proxy fetch + the service worker + a redeem endpoint, never the taOS API,
agents, secrets, or auth surface.

We achieve the separate origin with a second port (default 6970). But
that origin is cross-origin to the shell, so it never receives the
``taos_session`` cookie — which means it MUST have its own auth gate or
it becomes an open web proxy. We mirror the existing ``/shortcut/``
redeem-token pattern:

  * MAIN app (:6969) mints a short-lived signed ticket (authed endpoint
    ``/api/desktop/browser/proxy-ticket``).
  * This origin (:6970) redeems it at ``/__taos/redeem``, sets the
    ``taos_browser`` cookie, and 302s to the proxied URL.
  * Every other request on this origin requires a valid ``taos_browser``
    cookie or it is rejected with 401 (no login UI lives here, so we do
    NOT redirect to the main login).

State sharing
=============
The proxy needs the same browser cookie store / profile store / copilot
hub / auth manager as the main app so cookies and profiles stay
consistent across both origins. Rather than rebuild any of that, this
app *references the main app's ``State`` object directly* — ``app.state``
on the proxy app IS the main app's ``app.state``. The proxy app declares
no lifespan of its own; the main app's lifespan populates the shared
state before either server starts accepting requests.
"""
from __future__ import annotations

import logging
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser.proxy_ticket import (
    _PROXY_JTI_TRACKER,
    validate_proxy_ticket,
)

logger = logging.getLogger(__name__)

# Paths on the proxy origin that REQUIRE a valid taos_browser cookie.
# Everything else either has no cookie requirement (/__taos/redeem mints
# the cookie; /__taos/sw.js must be fetchable to install the SW) or has no
# handler at all (taOS API/auth routes are not mounted here → 404). We gate
# only the protected serving paths so unknown paths 404 naturally instead
# of leaking a 401 that would imply the route exists.
_PROTECTED_PATHS = frozenset({
    "/api/desktop/browser/proxy",
    "/__taos/copilot.js",
})

# In-memory taos_browser session store, keyed by a random session id.
# Maps session_id -> {user_id, expires_at}. Process-local — like the
# shortcut session store. Lives on app.state so both the redeem route and
# the auth gate share it; survives proxy-app rebuilds within a process.
_BROWSER_SESSION_IDLE_TTL = 3600  # 1 hour


def _session_store(state) -> dict:
    store = getattr(state, "browser_proxy_sessions", None)
    if store is None:
        store = {}
        state.browser_proxy_sessions = store
    return store


def _new_browser_session(state, user_id: str) -> str:
    session_id = secrets.token_urlsafe(32)
    _session_store(state)[session_id] = {
        "user_id": user_id,
        "expires_at": time.monotonic() + _BROWSER_SESSION_IDLE_TTL,
    }
    return session_id


def _resolve_browser_session(state, session_id: str) -> str | None:
    """Return the user_id for a valid taos_browser session, else None."""
    store = _session_store(state)
    entry = store.get(session_id)
    if entry is None:
        return None
    if time.monotonic() > entry["expires_at"]:
        store.pop(session_id, None)
        return None
    entry["expires_at"] = time.monotonic() + _BROWSER_SESSION_IDLE_TTL
    return entry["user_id"]


# The exact set of main-app state attributes the proxy origin legitimately
# needs. Anything outside this set raises AttributeError so a future proxy
# route cannot accidentally reach secrets, auth_manager, agent configs, etc.
_SHARED_STATE_ALLOWLIST: frozenset[str] = frozenset({
    "auth",
    "browser_cookie_store",
    "browser_proxy_signing_key",
    "browser_store",
    "copilot_hub",
    # Ports (non-sensitive) so the proxy fetch can build a frame-ancestors
    # CSP that lets the shell origin (main port) embed this proxy origin.
    "main_port",
    "browser_proxy_port",
})


class _SharedState:
    """A ``State``-like proxy that delegates attribute access to the main
    app's state, while allowing the proxy app to set its own attributes
    (e.g. ``browser_proxy_sessions``) without leaking them into the main
    app.

    Only attributes in ``_SHARED_STATE_ALLOWLIST`` are delegated to the
    shared state; everything else raises ``AttributeError`` so this origin
    cannot accidentally reach taOS internals (secrets, agent configs, etc.).
    """

    def __init__(self, shared) -> None:
        object.__setattr__(self, "_shared", shared)
        object.__setattr__(self, "_local", {})

    def __getattr__(self, name: str):
        local = object.__getattribute__(self, "_local")
        if name in local:
            return local[name]
        if name not in _SHARED_STATE_ALLOWLIST:
            raise AttributeError(
                f"_SharedState: '{name}' is not in the proxy-origin allowlist"
            )
        return getattr(object.__getattribute__(self, "_shared"), name)

    def __setattr__(self, name: str, value) -> None:
        object.__getattribute__(self, "_local")[name] = value


class BrowserProxyAuthMiddleware(BaseHTTPMiddleware):
    """Gate every request on a valid ``taos_browser`` cookie.

    Exempts only the redeem endpoint and the service worker script. On a
    missing/invalid cookie returns 401 with a clear message — never a
    redirect to the main login, since this origin has no login UI.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path not in _PROTECTED_PATHS:
            # Public (redeem, sw.js) or absent (taOS API/auth → 404 at the
            # router). Either way, no cookie gate here.
            return await call_next(request)

        session_id = request.cookies.get("taos_browser")
        if session_id and _resolve_browser_session(request.app.state, session_id):
            return await call_next(request)

        return JSONResponse(
            {
                "error": "browser_proxy_unauthenticated",
                "detail": (
                    "This origin serves only the taOS browser proxy and "
                    "requires a taos_browser cookie obtained via "
                    "/__taos/redeem. Open the browser from the taOS shell."
                ),
            },
            status_code=401,
        )


def _proxy_origin_current_user(request: Request) -> dict:
    """Dependency override for ``get_current_user`` on the proxy origin.

    The proxy route resolves the user from the ``taos_browser`` cookie
    (set at redeem time) rather than the ``taos_session`` cookie, which
    never reaches this cross-origin host. The auth middleware has already
    rejected requests without a valid cookie, so this is the same
    user_id the gate accepted.
    """
    from fastapi import HTTPException

    session_id = request.cookies.get("taos_browser", "")
    user_id = _resolve_browser_session(request.app.state, session_id) if session_id else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    auth_mgr = request.app.state.auth
    user = auth_mgr.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def create_browser_proxy_app(main_app_state) -> FastAPI:
    """Build the proxy-origin ASGI app, sharing *main_app_state*.

    Mounts ONLY the desktop_browser proxy routes (``/api/desktop/browser/proxy``,
    ``/__taos/sw.js``, ``/__taos/copilot.js``) plus the redeem route. It does
    NOT mount the taOS API, auth, agents, or any other routes — this origin
    is deliberately API-free.
    """
    # No docs/openapi: this origin exposes no documentable API surface and
    # we keep it minimal. The auth gate would 401 these anyway.
    app = FastAPI(
        title="taOS Browser Proxy Origin",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Share the main app's state so the browser cookie store, profile
    # store, copilot hub, auth manager, and the proxy signing key are all
    # the SAME objects across both origins. Local-only attributes (the
    # taos_browser session store) stay on this app via _SharedState.
    app.state = _SharedState(main_app_state)

    app.add_middleware(BrowserProxyAuthMiddleware)

    # Mount ONLY the three browser-proxy serving routes — NOT the whole
    # desktop_browser router (which also carries profile/bookmark/push/
    # capability/copilot APIs). This origin must stay API-free: the only
    # taOS-side surface here is the proxy fetch, the service worker, and
    # the in-page copilot script. We re-register the matching endpoint
    # functions on a fresh router and include THAT, so FastAPI wires this
    # app as the dependency-override provider for the proxy route.
    from fastapi import APIRouter
    from fastapi.routing import APIRoute

    from tinyagentos.routes.desktop_browser import router as desktop_browser_router

    _ALLOWED_PROXY_PATHS = frozenset({
        "/api/desktop/browser/proxy",
        "/__taos/sw.js",
        "/__taos/copilot.js",
    })
    proxy_router = APIRouter()
    for route in desktop_browser_router.routes:
        if isinstance(route, APIRoute) and route.path in _ALLOWED_PROXY_PATHS:
            proxy_router.add_api_route(
                route.path,
                route.endpoint,
                methods=list(route.methods or ["GET"]),
                include_in_schema=False,
            )
    app.include_router(proxy_router)

    # Resolve the proxy route's user from the taos_browser cookie instead
    # of taos_session (which never reaches this cross-origin host).
    app.dependency_overrides[get_current_user] = _proxy_origin_current_user

    @app.get("/__taos/redeem")
    async def redeem(
        request: Request,
        ticket: str = Query(..., description="Signed proxy ticket from the main app"),
        next: str = Query("/", description="On-origin proxy path to land on"),
    ):
        """Validate a ticket, set the taos_browser cookie, 302 to ``next``.

        ``next`` is validated to be an on-origin proxy path
        (``/api/desktop/browser/proxy?...``) so this can never be turned
        into an open redirect.
        """
        signing_key = getattr(request.app.state, "browser_proxy_signing_key", None)
        if not signing_key:
            return JSONResponse(
                {"error": "browser proxy not ready"}, status_code=503,
            )

        try:
            redeemed = validate_proxy_ticket(
                ticket, signing_key=signing_key, tracker=_PROXY_JTI_TRACKER,
            )
        except ValueError as exc:
            msg = str(exc)
            if "expired" in msg:
                detail = "ticket expired"
            elif "replay" in msg.lower():
                detail = "replay detected"
            else:
                detail = "invalid ticket"
            return JSONResponse({"error": detail}, status_code=403)

        # Validate next: must be a relative, on-origin proxy path. Reject
        # absolute URLs, scheme-relative (//host) and anything that isn't
        # the proxy fetch endpoint — no open redirects.
        if not _is_safe_next(next):
            return JSONResponse(
                {"error": "invalid redirect target"}, status_code=403,
            )

        session_id = _new_browser_session(request.app.state, redeemed.user_id)
        response = RedirectResponse(url=next, status_code=302)
        response.set_cookie(
            key="taos_browser",
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=_BROWSER_SESSION_IDLE_TTL,
            path="/",
        )
        # Prevent the single-use ticket in the redeem URL from leaking to the
        # proxied site via the Referer header on the subsequent navigation.
        response.headers["referrer-policy"] = "no-referrer"
        return response

    return app


def _is_safe_next(next_url: str) -> bool:
    """True only for relative on-origin proxy paths.

    Guards against open redirects: rejects absolute URLs, scheme-relative
    ``//host`` targets, and any path that isn't the proxy fetch endpoint.
    """
    if not next_url.startswith("/"):
        return False
    if next_url.startswith("//"):  # scheme-relative → off-origin
        return False
    parts = urlsplit(next_url)
    # A relative path has no scheme/netloc; urlsplit on "/a//b" keeps them
    # empty, but be defensive.
    if parts.scheme or parts.netloc:
        return False
    return parts.path == "/api/desktop/browser/proxy"
