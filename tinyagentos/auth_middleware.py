from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from tinyagentos.errors import error_response

EXEMPT_PATHS = {"/auth/login", "/auth/setup", "/auth/status", "/auth/me", "/auth/complete", "/auth/lock", "/api/health", "/api/version", "/api/cluster/workers", "/api/cluster/heartbeat", "/setup", "/setup/complete", "/redeem", "/api/desktop/browser/push/vapid-public-key", "/sw.js", "/desktop", "/desktop/index.html", "/chat-pwa"}
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
EXEMPT_PREFIXES = ("/static/", "/desktop/", "/chat-pwa/", "/ws/", "/shortcut/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth_mgr = request.app.state.auth
        path = request.url.path

        # Always allow exempt paths through (SPA shell, static assets, auth
        # endpoints, cluster heartbeat). Without this, a cached old client
        # could bypass onboarding by hitting an /api endpoint that the
        # not-configured branch used to allow through unconditionally.
        if path in EXEMPT_PATHS or any(path.startswith(p) for p in EXEMPT_PREFIXES):
            return await call_next(request)

        # Local token (Authorization: Bearer <token>) is accepted as a
        # substitute for the session cookie. The token lives at
        # {data_dir}/.auth_local_token, readable only by the user
        # running taOS, so possession = same-user-on-the-host trust.
        # Used by scripts and the upcoming CLI; the browser SPA keeps
        # using cookies.
        auth_header = request.headers.get("authorization", "")

        # Agent bearer-token path: Authorization: Bearer taos_agent_*
        if auth_header.startswith("Bearer taos_agent_"):
            plaintext = auth_header[len("Bearer "):]
            store = getattr(request.app.state, "agent_tokens_store", None)
            if store is None:
                return error_response(
                    status_code=503,
                    error="auth_unavailable",
                    detail="Token store is not initialised.",
                    fix="Wait for taOS startup to complete and retry.",
                    doc_url="/docs/agents/concepts/permissions",
                )
            row = await store.lookup_by_plaintext(plaintext)
            if row is None:
                return error_response(
                    status_code=401,
                    error="invalid_token",
                    detail="The bearer token is unknown or revoked.",
                    fix="Request a new token via POST /api/agents/{name}/token/issue.",
                    doc_url="/docs/agents/concepts/permissions",
                )
            request.state.user_id = row["user_id"]
            request.state.agent_id = row["agent_id"]
            request.state.token_scope = row["scope"]
            try:
                await store.touch_last_used(plaintext)
            except Exception:
                pass
            return await call_next(request)

        if auth_header.lower().startswith("bearer "):
            presented = auth_header[7:].strip()
            if presented and auth_mgr.validate_local_token(presented):
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
        if token and auth_mgr.validate_session(token) is not None:
            return await call_next(request)

        # Redirect to login for browsers, 401 for API calls
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            next_param = f"?next={path}" if path != "/" else ""
            return RedirectResponse(f"/auth/login{next_param}", status_code=303)

        return JSONResponse({"error": "Authentication required"}, status_code=401)
