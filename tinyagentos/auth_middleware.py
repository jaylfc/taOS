from __future__ import annotations

import ipaddress
import re
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

from tinyagentos.device_store import DEVICE_TOKEN_PREFIX

EXEMPT_PATHS = {"/auth/login", "/auth/setup", "/auth/status", "/auth/me", "/auth/complete", "/auth/lock", "/api/health", "/api/version", "/setup", "/setup/complete", "/redeem", "/api/desktop/browser/push/vapid-public-key", "/api/desktop/browser/proxy-config", "/sw.js", "/desktop", "/desktop/index.html", "/chat-pwa", "/app.html", "/manifest", "/api/agents/registry/pubkey", "/api/share/destinations"}

# Registry feed endpoints accept EITHER an admin session OR a registry JWT.
# When a Bearer token is present for these paths the request bypasses the
# session gate; the route handler verifies the JWT and grant itself.
_REGISTRY_FEED_PATHS = frozenset({
    "/api/agents/registry/revoked",
    "/api/agents/registry/grants",
})
# Read-only A2A bus proxy paths an agent may reach with its own registry JWT
# (scope a2a_receive, verified by the route).  Same passthrough contract as the
# feed paths: only a Bearer that is NOT the admin local token reaches the route.
_A2A_BUS_READ_PATHS = frozenset({
    "/api/a2a/bus/channels",
    "/api/a2a/bus/messages",
    "/api/a2a/bus/stream",
})
# Authenticated A2A bus WRITE path: an agent may POST here with its own registry
# JWT (scope a2a_send, verified by the route, which forces the bus `from` to the
# agent's own handle so it posts as itself instead of the owner's account).
_A2A_BUS_WRITE_PATHS = frozenset({
    "/api/a2a/bus/send",
})
# Observatory routes an agent may reach with its own registry JWT (scope
# observatory_control). The route verifies the JWT + grant itself; the
# middleware only passes the Bearer through. Admin/local-token is handled
# before this block so a local token is never mis-verified as a registry JWT.
_OBSERVATORY_PATHS = frozenset({
    "/api/observatory/pause",
    "/api/observatory/throttle",
    "/api/observatory/approval-mode",
    "/api/observatory/fleet",
})
# Every path that accepts a registry JWT in place of the admin session.  The
# passthrough is allowlisted to exactly these paths -- a registry JWT must never
# authenticate any other route (no skeleton key).
_AGENT_TOKEN_PATHS = _REGISTRY_FEED_PATHS | _A2A_BUS_READ_PATHS | _A2A_BUS_WRITE_PATHS | _OBSERVATORY_PATHS

# Project kanban routes an agent may reach with its own registry JWT (scope
# project_tasks, verified + project-bound by the route).  These are DYNAMIC
# paths (/api/projects/{pid}/tasks...), so an exact frozenset can't match them;
# a (method, compiled-regex) allowlist is used instead.  Each pattern is fully
# anchored and uses a slash-free segment ([^/]+) with an exact segment count, so
# sibling routes that must stay session-only -- /members, /relationships,
# /audit, /activity, project lifecycle -- never match. POST .../tasks IS
# reachable, but only with project_tasks_create, never with project_tasks.
# This is the project-scoped analogue of the exact _AGENT_TOKEN_PATHS contract:
# the token only reaches the handler, which then verifies the JWT + grant +
# project binding.  Anything not listed here is NOT reachable by a registry JWT.
_SEG = r"[^/]+"
_AGENT_TASK_ROUTES = (
    ("GET", re.compile(rf"^/api/projects/{_SEG}/tasks$")),
    # Task CREATION, gated by the SEPARATE project_tasks_create scope (not
    # project_tasks, which stays read + lifecycle + comments per Invariant 2+5).
    # Reaching the handler is not authorisation: it then verifies the JWT, the
    # project binding, and that narrower scope.
    ("POST", re.compile(rf"^/api/projects/{_SEG}/tasks$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/tasks/ready$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/tasks/{_SEG}$")),
    ("GET", re.compile(rf"^/api/projects/tasks/{_SEG}/context$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/tasks/{_SEG}/comments$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/tasks/{_SEG}/comments$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/tasks/{_SEG}/(claim|release|close|reopen)$")),
    # PATCH (free task-field mutation) was intentionally NOT here: it is broader
    # than the "read + lifecycle + comments" the project_tasks scope documents.
    # It is now reachable by a project_tasks_update-bound agent token, but the
    # route enforces the narrower scope + authorship/lead gate + a field
    # whitelist (title, body, labels, priority) before any mutation, so
    # a project_tasks worker still cannot rewrite fields it was never meant to.
    ("PATCH", re.compile(rf"^/api/projects/{_SEG}/tasks/{_SEG}$")),
    # Mark-claimable curation: reachable by a Bearer token, but the handler
    # (_authorize_project_lead) then restricts it to THIS project's LEAD agent --
    # a plain project_tasks worker is refused. Toggles only the "claimable"
    # label, so it does not widen the scope into free field edits (cf. PATCH).
    ("POST", re.compile(rf"^/api/projects/{_SEG}/tasks/{_SEG}/claimable$")),
)

# Project doc-review stamp store routes an agent may reach with its own registry
# JWT (scope project_doc_review, verified + project-bound by the route).  These
# are DYNAMIC paths (/api/projects/{pid}/doc-review/...), so a (method,
# compiled-regex) allowlist is used.  The single-doc path is `path`-typed:
# doc_path may contain slashes (e.g. src/foo.md), so the final segment is `(.+)`
# rather than the slash-free `[^/]+` used for the task routes.  Same contract as
# the task allowlist: the token only reaches the handler, which then verifies
# the JWT + grant + project binding; nothing else is reachable by the token.
_AGENT_DOC_REVIEW_ROUTES = (
    ("GET", re.compile(rf"^/api/projects/{_SEG}/doc-reviews$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/doc-review/(.+)$")),
    ("PUT", re.compile(rf"^/api/projects/{_SEG}/doc-review/(.+)$")),
)

# Project notes store routes an agent may reach with its own registry JWT
# (scope project_notes, verified + project-bound by the route).  The token only
# reaches the handler, which then verifies the JWT + grant + project binding;
# nothing else is reachable by the token.  List/create are on the collection
# segment; patch/delete target a specific note id.
_AGENT_NOTES_ROUTES = (
    ("GET", re.compile(rf"^/api/projects/{_SEG}/notes$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/notes$")),
    ("PATCH", re.compile(rf"^/api/projects/{_SEG}/notes/{_SEG}$")),
    ("DELETE", re.compile(rf"^/api/projects/{_SEG}/notes/{_SEG}$")),
)


def _is_agent_doc_review_path(method: str, path: str) -> bool:
    """True only for the doc-review routes a project_doc_review token may reach."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_DOC_REVIEW_ROUTES)


def _is_agent_notes_path(method: str, path: str) -> bool:
    """True only for the notes routes a project_notes token may reach.
    Strict method + anchored-regex match; everything else is excluded."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_NOTES_ROUTES)


_AGENT_CANVAS_ROUTES = (
    ("GET", re.compile(rf"^/api/projects/{_SEG}/canvas/elements$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/canvas/elements$")),
    ("PATCH", re.compile(rf"^/api/projects/{_SEG}/canvas/elements/{_SEG}$")),
    ("DELETE", re.compile(rf"^/api/projects/{_SEG}/canvas/elements/{_SEG}$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/canvas/snapshot\.png$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/canvas/snapshot\.tldr$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/canvas/stream$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/canvas/watch-projection$")),
)

# Decisions route an agent may reach with its own registry JWT (scope
# decisions_write). Create, answer (mirror), read-own, and list-own.  The route
# verifies the JWT + grant + project binding.
_AGENT_DECISIONS_ROUTES = (
    ("POST", re.compile(r"^/api/decisions$")),
    ("POST", re.compile(r"^/api/decisions/[^/]+/answer/agent$")),
    ("GET", re.compile(r"^/api/decisions/[^/]+/agent$")),
    ("GET", re.compile(r"^/api/decisions/agent$")),
)

# Device-bearer self-service paths (lock-screen push-token rotation plus
# decision list/get/answer). A scoped device token (Bearer taosdev_...) may
# pass through the auth gate on exactly these routes; the route dependency
# (current_user_or_device) resolves the device and synthesizes a NON-admin
# CurrentUser. request.state.user_id is left None on this path so device
# bearers cannot reach other current_user / request.state consumers (e.g.
# create_decision which reads uid=request.state.user_id). Session-authenticated
# calls still work: the session-cookie check runs when no Bearer header is
# present, so GET/POST without a Bearer reach the guard normally.
_DEVICE_BEARER_PATHS = (
    ("PATCH", re.compile(r"^/api/devices/[^/]+/push-token$")),
    ("GET", re.compile(r"^/api/decisions$")),
    ("GET", re.compile(r"^/api/decisions/[^/]+$")),
    ("GET", re.compile(r"^/api/decisions/[^/]+/history$")),
    ("POST", re.compile(r"^/api/decisions/[^/]+/answer$")),
)


def _is_device_bearer_path(method: str, path: str) -> bool:
    """True only for the exact device-bearer self-service routes. Strict
    method + anchored-regex match; everything else stays session-only."""
    return any(m == method and rx.match(path) for m, rx in _DEVICE_BEARER_PATHS)

# Project-files routes a files_read / files_write token may reach. Reads
# (list/watch/get/trash-list/stats) require a files_read grant; writes
# (upload/mkdir/delete/restore/purge/empty) require files_write. The route
# verifies the JWT + grant + project binding (slug resolves to the project).
_AGENT_FILES_ROUTES = (
    ("GET", re.compile(rf"^/api/projects/{_SEG}/files$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/files/watch$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/files/upload$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/mkdir$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/files/.+$")),
    ("DELETE", re.compile(rf"^/api/projects/{_SEG}/files/.+$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/trash$")),
    ("POST", re.compile(rf"^/api/projects/{_SEG}/trash/{_SEG}/restore$")),
    ("DELETE", re.compile(rf"^/api/projects/{_SEG}/trash/{_SEG}$")),
    ("DELETE", re.compile(rf"^/api/projects/{_SEG}/trash$")),
    ("GET", re.compile(rf"^/api/projects/{_SEG}/stats$")),
)

# Scope-request CREATE an agent may reach with its own registry JWT to ask for
# MORE scopes on its own identity. Only the create endpoint; the approve/deny
# subactions have extra path segments and are NOT matched here, so they stay
# owner/admin session-only. The route verifies the JWT identity == the path
# canonical_id (an agent may only self-request).
_AGENT_SCOPE_REQUEST_ROUTES = (
    ("POST", re.compile(rf"^/api/agents/registry/{_SEG}/scope-requests$")),
)


def _is_agent_task_path(method: str, path: str) -> bool:
    """True only for the exact subset of task routes a project_tasks token may
    reach.  Strict method + anchored-regex match; everything else is excluded."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_TASK_ROUTES)


def _is_agent_canvas_path(method: str, path: str) -> bool:
    """True only for the exact subset of canvas routes a canvas_read/canvas_write
    token may reach.  Strict method + anchored-regex match; the PATCH permissions
    route stays session-only (owner/admin only) but the PATCH elements route is
    reachable by a canvas_write-bound agent token, mirroring POST/DELETE."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_CANVAS_ROUTES)


def _is_agent_decisions_path(method: str, path: str) -> bool:
    """True only for the exact subset of decision routes an agent token may reach:
      - POST /api/decisions -> decisions_write (create)
      - POST /api/decisions/{id}/answer/agent -> decisions_write (mirror)
      - GET  /api/decisions/{id}/agent        -> decisions_write (read own)
      - GET  /api/decisions/agent             -> decisions_write (list own)
    The route verifies the JWT + grant + project binding."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_DECISIONS_ROUTES)


def _is_agent_files_path(method: str, path: str) -> bool:
    """True only for the project-files routes a files_read / files_write token
    may reach.  Strict method + anchored-regex match; the route verifies the
    JWT + grant + project binding."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_FILES_ROUTES)


def _is_agent_scope_request_path(method: str, path: str) -> bool:
    """True only for POST /api/agents/registry/{cid}/scope-requests, which an
    agent may reach with its own registry JWT to self-request more scopes. The
    route verifies the JWT identity == canonical_id; approve/deny are excluded
    (extra path segments) and stay owner/admin session-only."""
    return any(m == method and rx.match(path) for m, rx in _AGENT_SCOPE_REQUEST_ROUTES)
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
EXEMPT_PREFIXES = ("/static/", "/desktop/", "/chat-pwa/", "/ws/", "/shortcut/", "/api/peer/")

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
# Free-tier manual pairing: the worker polls manual-claim unauthenticated (the
# code it displayed is the proof). The matching authorize endpoint
# (/api/cluster/pairing/manual) stays admin-gated and is NOT exempt.
_CLUSTER_PAIRING_MANUAL_CLAIM = "/api/cluster/pairing/manual-claim"
_CLUSTER_WORKERS = "/api/cluster/workers"
_CLUSTER_HEARTBEAT = "/api/cluster/heartbeat"

# Project-invite redeem: unauthenticated (the PIN is the proof of possession,
# exactly like cluster pairing). The redeem POST and the content-negotiated
# GET /i/{id} are method-sensitive exemptions (mirror the pairing-claim
# pattern). Per-IP fixed-window rate limit reuses the pairing throttle below.
_INVITE_REDEEM = "/api/projects/invites/redeem"
_INVITE_INFO_PREFIX = "/i/"

# Local-only shutdown drain: the systemd ExecStop hook (taos-graceful-stop)
# POSTs this from localhost with no session cookie and no token, so it was
# getting 401 and the in-app drain never ran. We exempt it ONLY for loopback
# callers (127.0.0.1 / ::1) so a remote caller still hits the normal auth gate.
_PREPARE_SHUTDOWN = "/api/system/prepare-shutdown"


def _is_loopback_client(request: Request) -> bool:
    """Return True only when the request's immediate TCP peer is loopback.

    The controller binds 0.0.0.0, so it IS reachable remotely; the safety here
    does not come from the bind address. request.client.host is the immediate
    peer of the TCP connection (set by the ASGI server from the socket), which a
    remote caller cannot make 127.0.0.1 / ::1 -- they would have to be connecting
    over the loopback interface, i.e. already on the host. We deliberately do NOT
    consult X-Forwarded-For (taOS runs no trusted reverse proxy that would set
    it), so a remote caller cannot spoof loopback with a header. If a trusted
    proxy is ever placed in front, this check must be revisited.
    """
    client = request.client
    if client is None:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Per-IP fixed-window rate limiter (shared by unauthenticated proof-of-
# possession endpoints: cluster pairing manual-claim and project-invite redeem).
# ---------------------------------------------------------------------------
_INVITE_RATE_WINDOW_SECS = 10.0
_INVITE_RATE_MAX_PER_WINDOW = 20
# ip -> (window_start_ts, count). In-memory is sufficient: the controller is a
# single process and the cap only needs to bound a brute-force burst.
_rate_limit_hits: dict[str, tuple[float, int]] = {}


def rate_limit_ok(key: str, *, window_secs: float = _INVITE_RATE_WINDOW_SECS,
                  max_per_window: int = _INVITE_RATE_MAX_PER_WINDOW) -> bool:
    """Fixed-window per-key limiter. Returns False when the key has exceeded
    ``max_per_window`` requests in the current window. The pairing
    ``_manual_claim_rate_ok`` helper in cluster.py uses the identical contract;
    this shared copy keeps the cluster and invite paths from drifting and lets
    both pass the same ``20 per 10s`` cap the design specifies.

    Mirrors ``_manual_claim_rate_ok`` exactly so behaviour is identical.
    """
    now = time.time()
    window_start, count = _rate_limit_hits.get(key, (now, 0))
    if now - window_start >= window_secs:
        window_start, count = now, 0
    count += 1
    _rate_limit_hits[key] = (window_start, count)
    return count <= max_per_window


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
      POST /api/cluster/workers/{n}/incus-enroll — session-exempt, HMAC gate at route level
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
    if method == "POST" and path == _CLUSTER_PAIRING_MANUAL_CLAIM:
        return True
    # Cluster workers — GET is a public list; POST is session-exempt (HMAC gate).
    if method == "GET" and path == _CLUSTER_WORKERS:
        return True
    if method == "POST" and path == _CLUSTER_WORKERS:
        return True
    if method == "POST" and path == _CLUSTER_HEARTBEAT:
        return True
    # POST /api/cluster/workers/<name>/incus-enroll — session-exempt; the route
    # verifies the worker's HMAC signature (see tinyagentos.worker.enroll).
    if (
        method == "POST"
        and path.startswith(_CLUSTER_WORKERS + "/")
        and path.endswith("/incus-enroll")
    ):
        return True
    # Project-invite redeem: POST /api/projects/invites/redeem is
    # unauthenticated (the PIN is the proof of possession, exactly like cluster
    # pairing claim) and is per-IP rate-limited at the route layer.
    if method == "POST" and path == _INVITE_REDEEM:
        return True
    # GET /i/{invite_id} — content-negotiated invite advert (machine JSON or a
    # human HTML page). No PIN check here; it only advertises the redeem
    # contract. The browser-friendly /i/ exact path stays session-gated so a
    # logged-out admin is not exposed, but the invite id form is exempt.
    if method == "GET" and path.startswith(_INVITE_INFO_PREFIX) and "/" not in path[len(_INVITE_INFO_PREFIX):]:
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

        # Loopback-only shutdown drain: POST /api/system/prepare-shutdown from
        # the local systemd stop hook (curl on 127.0.0.1, no session/token).
        # Remote callers fall through to the normal session gate below.
        if (
            request.method == "POST"
            and path == _PREPARE_SHUTDOWN
            and _is_loopback_client(request)
        ):
            request.state.user_id = None
            request.state.is_admin = False
            request.state.via = "loopback"
            return await call_next(request)

        # Local token (Authorization: Bearer <token>) is accepted as a
        # substitute for the session cookie. The token lives at
        # {data_dir}/.auth_local_token, readable only by the user
        # running taOS, so possession = same-user-on-the-host trust.
        # Used by scripts and the upcoming CLI; the browser SPA keeps
        # using cookies.
        auth_header = request.headers.get("authorization", "")
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

        # Agent-token endpoints (registry feeds + A2A bus proxy + project kanban)
        # accept a registry JWT as an alternative to the admin session.  This
        # branch sits AFTER the local-token check on purpose: a local token is
        # admin-equivalent and must keep its admin semantics on these paths
        # (taOSmd polls the feeds with it today).  Only a Bearer that is NOT the
        # local token falls through to here; it is PASSED THROUGH and the route
        # verifies the registry JWT + scope grant (+ project binding for task
        # routes).  The allowlist -- the exact _AGENT_TOKEN_PATHS plus the
        # anchored task-route matcher -- is closed so a registry JWT can never
        # authenticate any other route (no skeleton key).
        if (
            path in _AGENT_TOKEN_PATHS
            or _is_agent_task_path(request.method, path)
            or _is_agent_doc_review_path(request.method, path)
            or _is_agent_notes_path(request.method, path)
            or _is_agent_canvas_path(request.method, path)
            or _is_agent_decisions_path(request.method, path)
            or _is_agent_files_path(request.method, path)
            or _is_agent_scope_request_path(request.method, path)
        ) and auth_header.lower().startswith("bearer "):
            request.state.user_id = None
            request.state.is_admin = False
            request.state.via = "registry_jwt_candidate"
            return await call_next(request)

        # Device-bearer self-service: a scoped device token may pass the auth
        # gate on the carded lock-screen routes. The middleware does NOT
        # resolve the device -- it only lets the Bearer through with
        # user_id=None (so current_user / request.state consumers stay
        # session-only, Invariant c). The route dependency
        # (current_user_or_device) resolves the token and synthesizes a
        # non-admin CurrentUser (Invariant a).
        if (
            _is_device_bearer_path(request.method, path)
            and auth_header.lower().startswith("bearer ")
            # Only a DEVICE token may take this passthrough. Matching any
            # bearer shadowed valid sessions: a logged-in user carrying an
            # unrelated Authorization header got 401 on every carded route,
            # because this branch sets user_id=None before the session was
            # ever consulted.
            and auth_header[7:].strip().startswith(DEVICE_TOKEN_PREFIX)
        ):
            request.state.user_id = None
            request.state.is_admin = False
            request.state.via = "device_bearer_candidate"
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
            user_agent = request.headers.get("user-agent", "")
            user_id = auth_mgr.validate_session(token, user_agent=user_agent)
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
