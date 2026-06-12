from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

EXEMPT_PATHS = {"/auth/login", "/auth/setup", "/auth/status", "/auth/me", "/auth/complete", "/auth/lock", "/api/health", "/api/version", "/setup", "/setup/complete", "/redeem", "/api/desktop/browser/push/vapid-public-key", "/api/desktop/browser/proxy-config", "/sw.js", "/desktop", "/desktop/index.html", "/chat-pwa", "/api/agents/registry/pubkey"}

# Registry feed endpoints accept EITHER an admin session OR a registry JWT.
# When a Bearer token is present for these paths the request bypasses the
# session gate; the route handler verifies the JWT and grant itself.
_REGISTRY_FEED_PATHS = frozenset({
    "/api/agents/registry/revoked",
    "/api/agents/registry/grants",
})
# Bundle assets and the SPA shell HTML must be reachable without auth so:
#   1. The browser can install and cache the shell for offline / PWA use.
#   2. After a backend restart the cached shell loads immediately without
#      a round-trip that would return 401 and leave the user with a blank
#      screen instead of the cached app.
# Auth is enforced client-side: the SPA checks /auth/status on boot and
# redirects to /auth/login if there is no valid session — so dropping the
# server-side gate on the HTML does not reduce security.
# Stale-bundle risk is mitigated by __TAOS_VERSION__-namespaced SW caches:
# on activate the SW deletes any cache that does not match the current
# build token, so stale index.html entries are evicted automatically.
# /shortcut/ routes use their own taos_shortcut session cookie for auth;
# they are intentionally excluded from the main session gate here.
# /ws/ routes validate the taos_session cookie inside each endpoint handler,
# before websocket.accept() and before spawning any process. BaseHTTPMiddleware
# wrapping a WS upgrade can cause connection-level issues in some Starlette
# versions, so /ws/ remains exempt at the middleware layer; the per-endpoint
# check is the authoritative guard for all WebSocket endpoints.
EXEMPT_PREFIXES = ("/static/", "/desktop/", "/chat-pwa/", "/ws/", "/shortcut/")

# Consent-loop status-poll paths are unauthenticated (the opaque request_id is
# the capability), but the sub-action paths (/approve, /deny) require admin
# auth.  We exempt GET requests to /api/agents/auth-requests/<id> specifically
# so the external agent can poll without credentials, while ensuring that POST
# requests to /approve and /deny still require a session cookie.
_AUTH_REQUEST_BASE = "/api/agents/auth-requests"
_AUTH_REQUEST_PREFIX = "/api/agents/auth-requests/"

# Cluster pairing: announce and claim are unauthenticated (the pairing code is
# the proof of possession).  Pending and confirm require an admin session and
# are NOT exempt.  Worker register and heartbeat are session-exempt because the
# route-level HMAC dependency is the gate; GET workers is public.
_CLUSTER_PAIRING_ANNOUNCE = "/api/cluster/pairing/announce"
_CLUSTER_PAIRING_CLAIM = "/api/cluster/pairing/claim"
_CLUSTER_WORKERS = "/api/cluster/workers"
_CLUSTER_HEARTBEAT = "/api/cluster/heartbeat"


def _is_exempt(method: str, path: str) -> bool:
    """Return True if this request should bypass the auth gate.

    Consent-loop exemptions (method-sensitive):
      POST /api/agents/auth-requests          — create request, no auth needed
      GET  /api/agents/auth-requests/{id}     — status poll, no auth needed
      POST /api/agents/auth-requests/{id}/approve|deny — admin only, NOT exempt
      GET  /api/agents/auth-requests          — list (admin), NOT exempt

    Cluster pairing exemptions (method-sensitive):
      POST /api/cluster/pairing/announce      — unauthenticated, code hash is proof
      POST /api/cluster/pairing/claim         — unauthenticated, code is proof
      GET  /api/cluster/workers               — public worker list
      POST /api/cluster/workers               — session-exempt, HMAC gate at route level
      POST /api/cluster/heartbeat             — session-exempt, HMAC gate at route level
    """
    if path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES):
        return True
    # POST /api/agents/auth-requests (exact) — external agent creates a request.
    if method == "POST" and path == _AUTH_REQUEST_BASE:
        return True
    # GET /api/agents/auth-requests/<id> — status poll; only when there's a
    # single path segment after the prefix (no further slashes → not a subaction).
    if method == "GET" and path.startswith(_AUTH_REQUEST_PREFIX):
        tail = path[len(_AUTH_REQUEST_PREFIX):]
        if tail and "/" not in tail:
            return True
    # Cluster pairing — announce and claim are unauthenticated.
    if method == "POST" and path == _CLUSTER_PAIRING_ANNOUNCE:
        return True
    if method == "POST" and path == _CLUSTER_PAIRING_CLAIM:
        return True
    # Cluster workers — GET is a public list; POST is session-exempt (HMAC gate).
    if method == "GET" and path == _CLUSTER_WORKERS:
        return True
    if method == "POST" and path == _CLUSTER_WORKERS:
        return True
    if method == "POST" and path == _CLUSTER_HEARTBEAT:
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_mgr = request.app.state.auth
        path = request.url.path

        # Always allow exempt paths through (SPA shell, static assets, auth
        # endpoints, cluster heartbeat). Without this, a cached old client
        # could bypass onboarding by hitting an /api endpoint that the
        # not-configured branch used to allow through unconditionally.
        if _is_exempt(request.method, path):
            request.state.user_id = None
            request.state.is_admin = False
            request.state.via = "exempt"
            return await call_next(request)

        # Registry feed endpoints (revoked + grants) accept a registry JWT as an
        # alternative to the admin session.  When a Bearer token is present for
        # these paths, pass the request through to the route handler, which calls
        # _check_feed_token() to verify the JWT and the registry_feeds_read grant.
        # The route also falls back to request.state.is_admin for admin sessions,
        # so normal cookie-authenticated admin access continues to work.
        auth_header = request.headers.get("authorization", "")
        if path in _REGISTRY_FEED_PATHS and auth_header.lower().startswith("bearer "):
            request.state.user_id = None
            request.state.is_admin = False
            request.state.via = "registry_jwt_candidate"
            return await call_next(request)

        # Local token (Authorization: Bearer <token>) is accepted as a
        # substitute for the session cookie. The token lives at
        # {data_dir}/.auth_local_token, readable only by the user
        # running taOS, so possession = same-user-on-the-host trust.
        # Used by scripts and the upcoming CLI; the browser SPA keeps
        # using cookies.
        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
            if presented and auth_mgr.validate_local_token(presented):
                # A valid local token IS valid auth (same-host trust: the
                # token file is 0600, possession = the host user). It maps to
                # the primary/admin user when one exists. Before onboarding
                # there is no primary user yet — the token still passes (it is
                # how scripts/CLI operate pre-setup), but with no user_id, so
                # current_user-gated routes still 401 while middleware-only
                # routes proceed as before. (Not failing closed here: the
                # local token already authenticates, so it is not a bypass.)
                primary = auth_mgr.get_primary_user()
                if primary:
                    request.state.user_id = primary["id"]
                    request.state.is_admin = True
                    request.state.via = "local_token"
                else:
                    request.state.user_id = None
                    request.state.is_admin = False
                    request.state.via = "local_token"
                return await call_next(request)

        # First boot: no user yet. Browsers go to the setup page; APIs
        # hard-fail so a stale cached client knows to refresh.
        if not auth_mgr.is_configured():
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                return RedirectResponse("/auth/setup", status_code=303)
            return JSONResponse(
                {"error": "onboarding_required", "needs_onboarding": True},
                status_code=401,
            )

        # Check session cookie
        token = request.cookies.get("taos_session")
        if token:
            user_id = auth_mgr.validate_session(token)
            if user_id is not None:
                user_record = auth_mgr.get_user_by_id(user_id)
                request.state.user_id = user_id
                request.state.is_admin = bool(
                    user_record.get("is_admin") if user_record else False
                )
                request.state.via = "session"
                return await call_next(request)

        # Redirect to login for browsers, 401 for API calls
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            next_param = f"?next={path}" if path != "/" else ""
            return RedirectResponse(f"/auth/login{next_param}", status_code=303)

        return JSONResponse({"error": "Authentication required"}, status_code=401)
