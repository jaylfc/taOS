"""Proxy for the taOSgo account service (taos.my) -- taOSgo Phase 1.

The taOS client (Settings > Account, the off-network screen) calls same-origin
/api/account/* so the taos.my base URL stays server-side and there is no CORS.
We forward to {TAOS_ACCOUNT_BASE_URL}/api/auth/* with cookie pass-through both
ways, so the taos.my session cookie round-trips through this host origin.

The taOS online account is a core feature for every instance, so the base URL
defaults to https://taos.my; TAOS_ACCOUNT_BASE_URL stays an override for local or
staging testing. (A blank override still yields a 503 'service unavailable'.)
"""
from __future__ import annotations

import os
import re

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

router = APIRouter()

# Only these account actions are proxied. The upstream base is operator config
# (env), never user input, so there is no open-proxy / SSRF surface.
_ACTIONS: dict[str, tuple[str, str]] = {
    "me": ("GET", "/api/auth/me"),
    "login": ("POST", "/api/auth/login"),
    "register": ("POST", "/api/auth/register"),
    "logout": ("POST", "/api/auth/logout"),
}

_TIMEOUT = httpx.Timeout(15.0)


# The taOS online account lives at taos.my for every instance; it is a core
# product feature, not per-deployment config. Default to it; the env var is an
# override for local/staging only.
_DEFAULT_ACCOUNT_BASE_URL = "https://taos.my"


def _base_url() -> str | None:
    raw = os.environ.get("TAOS_ACCOUNT_BASE_URL")
    if raw is None:
        # Unset: use the production account service. This is the normal path.
        return _DEFAULT_ACCOUNT_BASE_URL
    # An explicit (possibly blank) override: blank disables the proxy (503),
    # which dev/test use to exercise the unavailable state.
    return raw.strip().rstrip("/") or None


def _trust_forwarded_proto() -> bool:
    """X-Forwarded-Proto is client-spoofable unless a trusted proxy sets it. Only
    honor it when the deployment opts in (the taOSgo relay, which terminates TLS
    and forwards over http, sets TAOS_TRUST_FORWARDED_PROTO=1)."""
    return os.environ.get("TAOS_TRUST_FORWARDED_PROTO", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _append_header(resp: Response, name: str, value: str) -> None:
    """Append a raw response header, skipping values that are not latin-1
    encodable. HTTP/1.1 header bytes are latin-1; a non-encodable relayed value
    would otherwise raise UnicodeEncodeError and break the whole response."""
    try:
        resp.raw_headers.append((name.encode("latin-1"), value.encode("latin-1")))
    except UnicodeEncodeError:
        pass


def _rewrite_set_cookie(value: str, secure_ok: bool) -> str:
    """Rescope an upstream Set-Cookie to this proxy origin so the browser
    accepts it: drop the Domain attribute (the cookie was issued for taos.my but
    the browser is talking to this host), and drop Secure when the proxy
    connection is not HTTPS, since a Secure cookie is rejected over plain HTTP."""
    kept: list[str] = []
    for part in value.split(";"):
        p = part.strip()
        low = p.lower()
        if low.startswith("domain="):
            continue
        if low == "secure" and not secure_ok:
            continue
        kept.append(p)
    return "; ".join(kept)


async def _forward_to(request: Request, method: str, path: str) -> Response:
    base = _base_url()
    if base is None:
        return JSONResponse(
            {"error": "account service not configured"}, status_code=503
        )
    headers: dict[str, str] = {}
    cookie = request.headers.get("cookie")
    if cookie:
        headers["Cookie"] = cookie
    body: bytes | None = None
    if method == "POST":
        body = await request.body()
        ctype = request.headers.get("content-type")
        if ctype:
            headers["Content-Type"] = ctype
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            upstream = await http.request(
                method, base + path, content=body, headers=headers
            )
    except httpx.HTTPError:
        return JSONResponse(
            {"error": "account service unreachable"}, status_code=503
        )
    # Relay the upstream body + content-type verbatim (do not assume JSON), so
    # error pages, redirects, and non-JSON bodies pass through unmangled.
    resp = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )
    # Derive Secure from the real connection scheme, and from X-Forwarded-Proto
    # only when the deployment trusts it (the TLS-terminating taOSgo relay
    # forwards over http). Untrusted, the header is client-spoofable so ignore it.
    fwd = ""
    if _trust_forwarded_proto():
        fwd = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    secure_ok = request.url.scheme == "https" or fwd == "https"
    # Relay the session cookie (rescoped to this origin) plus a small allowlist of
    # response headers so redirects (Location) and auth challenges survive.
    _RELAY = {"location", "cache-control", "www-authenticate"}
    for name, value in upstream.headers.multi_items():
        low = name.lower()
        if low == "set-cookie":
            _append_header(resp, "set-cookie", _rewrite_set_cookie(value, secure_ok))
        elif low in _RELAY:
            _append_header(resp, low, value)
    return resp


async def _forward(request: Request, action: str) -> Response:
    method, path = _ACTIONS[action]
    return await _forward_to(request, method, path)


# A join request_id reaches the upstream URL path, so it must be a simple opaque
# token, never something that can inject path segments or query (../, %2f, ?).
_RID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _valid_rid(rid: str) -> bool:
    return bool(_RID_RE.match(rid))


@router.get("/api/account/me")
async def account_me(request: Request):
    return await _forward(request, "me")


@router.post("/api/account/login")
async def account_login(request: Request):
    return await _forward(request, "login")


@router.post("/api/account/register")
async def account_register(request: Request):
    return await _forward(request, "register")


@router.post("/api/account/logout")
async def account_logout(request: Request):
    return await _forward(request, "logout")


# --- taOSgo cluster-join (consent-join) proxy ---
# Same cookie-passthrough pattern as the auth actions above: the taOS client
# calls same-origin /api/account/cluster/join/* and we forward to the taos.my
# /api/cluster/join/* endpoints (taos-website PR #35) so the session cookie
# round-trips through this origin. The signed-in worker app POSTs a join request;
# the user approves/denies from inside taOS; the app polls for the preauth key.
_JOIN_BASE = "/api/cluster/join"


@router.post("/api/account/cluster/join/request")
async def cluster_join_request(request: Request):
    return await _forward_to(request, "POST", f"{_JOIN_BASE}/request")


@router.get("/api/account/cluster/join/requests")
async def cluster_join_requests(request: Request):
    return await _forward_to(request, "GET", f"{_JOIN_BASE}/requests")


@router.post("/api/account/cluster/join/requests/{rid}/approve")
async def cluster_join_approve(request: Request, rid: str):
    if not _valid_rid(rid):
        return JSONResponse({"error": "invalid request id"}, status_code=400)
    return await _forward_to(request, "POST", f"{_JOIN_BASE}/requests/{rid}/approve")


@router.post("/api/account/cluster/join/requests/{rid}/deny")
async def cluster_join_deny(request: Request, rid: str):
    if not _valid_rid(rid):
        return JSONResponse({"error": "invalid request id"}, status_code=400)
    return await _forward_to(request, "POST", f"{_JOIN_BASE}/requests/{rid}/deny")


@router.get("/api/account/cluster/join/requests/{rid}/poll")
async def cluster_join_poll(request: Request, rid: str):
    if not _valid_rid(rid):
        return JSONResponse({"error": "invalid request id"}, status_code=400)
    return await _forward_to(request, "GET", f"{_JOIN_BASE}/requests/{rid}/poll")
