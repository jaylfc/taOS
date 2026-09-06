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

import asyncio
import json
import logging
import os
import re
import time

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from tinyagentos.taosnet import mesh, mesh_credentials
from tinyagentos.issued_cookies import TAOS_ISSUED_COOKIES
from tinyagentos.peer import resolve_local_identity_id

logger = logging.getLogger(__name__)

router = APIRouter()

# Per-host service tokens the join ready payload carries. They are persisted
# server-side and stripped from the browser-facing poll body so a bearer
# credential never sits in browser JavaScript.
_JOIN_SERVICE_TOKENS = ("controller_token", "sites_token")
# Everything stripped from the browser body: the service tokens plus the
# single-use Headscale preauth key, which the controller consumes server-side to
# join the mesh (Slice 2). ``headscale_preauth_key`` is the host join key;
# ``preauth_key`` is the guest preauth key (C2 cross-user transport). None of
# these should ever reach browser JavaScript.
_STRIP_KEYS = _JOIN_SERVICE_TOKENS + ("headscale_preauth_key", "preauth_key", "guest_preauth_key")

# Only these account actions are proxied. The upstream base is operator config
# (env), never user input, so there is no open-proxy / SSRF surface.
_ACTIONS: dict[str, tuple[str, str]] = {
    "me": ("GET", "/api/auth/me"),
    "login": ("POST", "/api/auth/login"),
    "register": ("POST", "/api/auth/register"),
    "logout": ("POST", "/api/auth/logout"),
    # Hub identity directory (hub social slice 1). The client calls same-origin
    # /api/account/hub/identity/* and we forward to {base}/api/hub/identity/*
    # with the session cookie pass-through, exactly like the auth and subdomain
    # actions above. The taos.my side (tables + register/lookup/rotate with
    # challenge proof) is the contract; these are its controller-side entries.
    "hub_identity_register": ("POST", "/api/hub/identity/register"),
    "hub_identity_lookup": ("GET", "/api/hub/identity/lookup"),
    "hub_identity_rotate": ("POST", "/api/hub/identity/rotate"),
    # Hub social slice 3: friend-request brokering, accepted-edge rows, and
    # presence heartbeat/lookup along edges (design "Directory API surface").
    # The client calls same-origin /api/account/hub/* and we forward to
    # {base}/api/hub/* with the session cookie pass-through, exactly like the
    # identity actions above. The taos.my side (requests inbox, accepted-edge
    # rows, presence TTL) is the contract; these are its controller-side entries.
    "hub_requests_post": ("POST", "/api/hub/requests"),
    "hub_requests_get": ("GET", "/api/hub/requests"),
    # hub_request_accept/decline carry a {id} segment appended in the handler.
    "hub_request_accept": ("POST", "/api/hub/requests"),
    "hub_request_decline": ("POST", "/api/hub/requests"),
    "hub_presence_post": ("POST", "/api/hub/presence"),
    "hub_presence_get": ("GET", "/api/hub/presence"),
    # Block asks the hub to sever the accepted edge (no more presence/hints).
    "hub_edge_revoke": ("POST", "/api/hub/edges/revoke"),
    # Hub sealed-envelope relay (cross-user collab A3): store-and-forward
    # through taos.my.  The node seals with the recipient's X25519 public key;
    # the hub never sees plaintext.  Recipients poll via the account proxy.
    "hub_relay_drop": ("POST", "/api/hub/relay/drop"),
    "hub_relay_poll": ("GET", "/api/hub/relay/poll"),
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


# Cookies this origin issues. The browser presents them on every same-origin
# /api/account/* call, but none may be relayed to the upstream taos.my: a
# taos.my log leak or compromise would otherwise expose valid local admin
# session tokens -- and, until this was fixed, the CSRF token that satisfies
# verify_csrf() for this origin. Only the cookies that belong upstream are
# forwarded. See tinyagentos/issued_cookies.py for why this is a deny-list.
_LOCAL_COOKIES = TAOS_ISSUED_COOKIES


def _strip_local_session_cookie(cookie_header: str) -> str | None:
    """Return ``cookie_header`` with every taOS-issued cookie removed.

    Parses the incoming Cookie header and drops every cookie this origin issues,
    preserving the rest (the upstream taos.my session cookie, etc.). Returns
    ``None`` when no cookies remain, so the relayed request sends no Cookie
    header at all. A malformed Cookie header is returned untouched rather than
    dropping unrelated cookies.
    """
    kept: list[str] = []
    for part in cookie_header.split(";"):
        p = part.strip()
        if not p:
            continue
        name = p.split("=", 1)[0].strip().lower()
        if name in _LOCAL_COOKIES:
            continue
        kept.append(p)
    if not kept:
        return None
    return "; ".join(kept)


async def _forward_to(
    request: Request, method: str, path: str, *, body: bytes | None = None
) -> Response:
    base = _base_url()
    if base is None:
        return JSONResponse(
            {"error": "account service not configured"}, status_code=503
        )
    headers: dict[str, str] = {}
    cookie = request.headers.get("cookie")
    if cookie:
        relayed = _strip_local_session_cookie(cookie)
        if relayed:
            headers["Cookie"] = relayed
    if body is None and method == "POST":
        # Default: relay the incoming request body verbatim (slice 1/2 actions).
        body = await request.body()
        ctype = request.headers.get("content-type")
        if ctype:
            headers["Content-Type"] = ctype
    elif body is not None:
        # Caller-supplied body (e.g. a signed statement the controller built):
        # always JSON, so set the content type for it.
        headers["Content-Type"] = "application/json"
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


# Hub recipient usernames carry a "hub:" prefix (e.g. "hub:hogne") so they
# are distinguishable from other identity namespaces.  The prefix is fixed;
# the suffix is the same alphanumeric + hyphen/underscore token shape as _RID_RE.
_HUB_RECIPIENT_RE = re.compile(r"^hub:[A-Za-z0-9_-]{1,64}$")


def _valid_hub_recipient(recipient: str) -> bool:
    return bool(_HUB_RECIPIENT_RE.match(recipient))


# --- Account subdomain actions (account model slice 3) ---
# Proxy to the taos.my subdomain claims service (slices 1 and 2 of the account
# model). The client calls same-origin /api/account/subdomains/*; we forward to
# {base}/api/subdomains/* with the session cookie pass-through, exactly like the
# auth and cluster-join actions above. The `name` field is validated as a simple
# rid-style token before it can reach the upstream URL, so a crafted name can
# never inject path segments or query (../, %2f, ?) into the forwarded request.
@router.get("/api/account/subdomains/check")
async def subdomains_check(request: Request):
    name, err = await _validate_subdomain_name(request)
    if err is not None:
        return err
    return await _forward_to(request, "GET", f"/api/subdomains/check?name={name}")


@router.post("/api/account/subdomains/claim")
async def subdomains_claim(request: Request):
    name, err = await _validate_subdomain_name(request)
    if err is not None:
        return err
    return await _forward_to(request, "POST", "/api/subdomains/claim")


@router.post("/api/account/subdomains/release")
async def subdomains_release(request: Request):
    name, err = await _validate_subdomain_name(request)
    if err is not None:
        return err
    return await _forward_to(request, "POST", "/api/subdomains/release")


async def _validate_subdomain_name(request: Request) -> tuple[str | None, Response | None]:
    """Validate a subdomain `name` (query param for GET, JSON `name` in the body
    for POST) as a simple rid-style token. Returns ``(name, None)`` on success,
    or ``(None, error_response)`` when the name is missing or malformed. A bad
    name must never reach the upstream, so it is rejected here with 400 and no
    forwarding happens."""
    if request.method == "GET":
        name = request.query_params.get("name", "")
    else:
        try:
            payload = await request.json()
        except Exception:  # noqa: BLE001
            return None, JSONResponse({"error": "invalid body"}, status_code=400)
        if not isinstance(payload, dict):
            return None, JSONResponse({"error": "invalid body"}, status_code=400)
        name = payload.get("name", "")
    if not _valid_rid(str(name)):
        return None, JSONResponse({"error": "invalid name"}, status_code=400)
    return str(name), None


# --- Hub identity directory actions (hub social slice 1) ---
# Anchor the node's minted keypairs to the account's username, look an identity's
# keys + key log up, and rotate keys. The client calls same-origin
# /api/account/hub/identity/*; we forward to {base}/api/hub/identity/* with the
# session cookie pass-through, so no new auth surface appears on the client and
# the taos.my base URL stays server-side. `username` on lookup is validated as a
# simple rid-style token before it can reach the upstream URL, so a crafted
# username can never inject path segments or query (../, %2f, ?).
@router.post("/api/account/hub/identity/register")
async def hub_identity_register(request: Request):
    return await _forward(request, "hub_identity_register")


@router.get("/api/account/hub/identity/lookup")
async def hub_identity_lookup(request: Request):
    username = request.query_params.get("username", "")
    if not _valid_rid(str(username)):
        return JSONResponse({"error": "invalid username"}, status_code=400)
    _method, path = _ACTIONS["hub_identity_lookup"]
    return await _forward_to(request, "GET", f"{path}?username={username}")


@router.post("/api/account/hub/identity/rotate")
async def hub_identity_rotate(request: Request):
    return await _forward(request, "hub_identity_rotate")

# --- Hub social slice 3: friend-request brokering + presence (directory) ---
# Same cookie-passthrough pattern as the identity actions above. The client calls
# same-origin /api/account/hub/requests* and /api/account/hub/presence*; we
# forward to {base}/api/hub/* with the session cookie relayed, so no new auth
# surface appears on the client and the taos.my base URL stays server-side.
# `rid` on dispositions is validated as a rid-style token before it can reach the
# upstream URL; `username` on presence lookup is likewise validated so a crafted
# username can never inject path segments or query (../, %2f, ?).
@router.post("/api/account/hub/requests")
async def hub_requests_post(request: Request):
    return await _forward(request, "hub_requests_post")

@router.get("/api/account/hub/requests")
async def hub_requests_get(request: Request):
    return await _forward(request, "hub_requests_get")

@router.post("/api/account/hub/requests/{rid}/accept")
async def hub_request_accept(request: Request, rid: str):
    if not _valid_rid(rid):
        return JSONResponse({"error": "invalid request id"}, status_code=400)
    _method, path = _ACTIONS["hub_request_accept"]
    return await _forward_to(request, "POST", f"{path}/{rid}/accept")

@router.post("/api/account/hub/requests/{rid}/decline")
async def hub_request_decline(request: Request, rid: str):
    if not _valid_rid(rid):
        return JSONResponse({"error": "invalid request id"}, status_code=400)
    _method, path = _ACTIONS["hub_request_decline"]
    return await _forward_to(request, "POST", f"{path}/{rid}/decline")

@router.post("/api/account/hub/presence")
async def hub_presence_post(request: Request):
    return await _forward(request, "hub_presence_post")

@router.get("/api/account/hub/presence")
async def hub_presence_get(request: Request):
    username = request.query_params.get("username", "")
    if not _valid_rid(str(username)):
        return JSONResponse({"error": "invalid username"}, status_code=400)
    _method, path = _ACTIONS["hub_presence_get"]
    return await _forward_to(request, "GET", f"{path}?username={username}")

@router.post("/api/account/hub/edges/revoke")
async def hub_edge_revoke(request: Request):
    return await _forward(request, "hub_edge_revoke")


# --- Hub sealed-envelope relay (cross-user collab A3) ---
# The node seals an inner payload to the recipient's X25519 public key and
# drops it at the hub; the recipient polls for queued envelopes.  The hub
# never sees plaintext — it only inspects ``recipient`` for routing.

@router.post("/api/account/hub/relay/drop")
async def hub_relay_drop(request: Request):
    body = await request.body()
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid body"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid body"}, status_code=400)
    recipient = str(payload.get("recipient", ""))
    if not _valid_hub_recipient(recipient):
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    # No recipient-binding on drop — this is outbound send; a node
    # drops envelopes addressed to any valid hub recipient.  Binding
    # lives in hub_relay_poll where a node polls its own queue.
    return await _forward_to(request, "POST", _ACTIONS["hub_relay_drop"][1], body=body)


@router.get("/api/account/hub/relay/poll")
async def hub_relay_poll(request: Request):
    """Poll for queued envelopes addressed to ``recipient``.

    The ``recipient`` query param is a validated hub username (e.g.
    ``hub:hogne``).  The hub returns the sealed envelopes; the caller
    unseals locally with its X25519 private key.
    """
    recipient = request.query_params.get("recipient", "")
    if not _valid_hub_recipient(str(recipient)):
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    # Bind the queried recipient to the caller's hub identity — a node
    # may only poll envelopes addressed to its own identity.
    local_id = await asyncio.to_thread(resolve_local_identity_id, request.app.state.data_dir)
    if local_id is not None and recipient != local_id:
        return JSONResponse(
            {"error": "recipient does not match local identity"},
            status_code=403,
        )
    _method, path = _ACTIONS["hub_relay_poll"]
    return await _forward_to(request, "GET",
                             f"{path}?recipient={recipient}")


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


@router.post("/api/account/cluster/join/guest-preauth")
async def cluster_guest_preauth(request: Request):
    """Mint a scoped guest preauth key for a cross-user collaborator instance.

    Called by the host at delegation-accept time (D1). Forwards to taos.my's
    ``POST /api/cluster/join/guest-preauth``, which creates an ACL-pinned
    (``tag:guest``) single-use preauth key via the Headscale admin API.

    **Security:** validates the ``contact_id`` *format* at the edge (length,
    prefix) before forwarding to avoid wasted upstream API calls, but does
    **not** assert the caller owns that contact — contact-to-caller ownership
    is enforced by the upstream taos.my service which has access to the
    contacts database.  Every preauth key is stripped from the response so no
    credential reaches browser JavaScript. The caller (peer channel, D1) must
    extract the key server-side before the response is sent to the browser, or
    use an alternative internal delivery path.
    """
    # Validate contact_id format at the edge (defense-in-depth: the upstream
    # also validates + enforces ownership, but we reject garbage at the edge
    # to avoid wasted API calls).
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid body"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("contact_id"), str):
        return JSONResponse({"error": "missing contact_id"}, status_code=400)
    cid: str = body["contact_id"].strip()
    if not cid.startswith("hub:") or len(cid) < 5 or len(cid) > 256:
        return JSONResponse({"error": "invalid contact_id"}, status_code=400)

    # Forward to taos.my with the validated body.
    resp = await _forward_to(request, "POST", f"{_JOIN_BASE}/guest-preauth",
                             body=json.dumps({"contact_id": cid}).encode("utf-8"))

    # Strip the preauth key from the response so it never reaches the browser.
    # Capture the join_intent for out-of-band delivery to the guest instance
    # via the peer channel (D1 delegation-accept handler calls
    # _pop_guest_preauth_intent to consume-and-delete).
    stripped, join_intent = _persist_join_credentials(resp)
    if join_intent:
        now = time.monotonic()
        # Sweep stale entries to bound dict growth even when consumer is inactive.
        expired = [k for k, (ts, _) in _guest_preauth_intents.items() if now - ts > _GUEST_PREAUTH_TTL_SECONDS]
        for k in expired:
            _guest_preauth_intents.pop(k, None)
        _guest_preauth_intents[cid] = (now, join_intent)
    return stripped


def _persist_join_credentials(resp: Response) -> tuple[Response, dict | None]:
    """When a poll response carries the per-host service tokens, persist them
    server-side (host-bound) and return a copy with those tokens + the single-use
    preauth key ALWAYS stripped, so nothing secret reaches browser JavaScript.

    Returns ``(browser_response, join_intent)``. ``join_intent`` is
    ``{"preauth_key", "hostname"}`` when the ready payload carried a preauth key
    for the caller to consume server-side (trigger the mesh-join), else None.

    Security ordering: stripping is decoupled from persistence and from the join.
    Once a 200 JSON body is seen to contain a service token, the secrets are
    always stripped, whether or not the save/join succeeds. A non-200 body, or one
    that does not parse to a dict carrying a service token, is returned untouched.
    Parsing does not depend on the Content-Type header. Relayed headers
    (Set-Cookie etc.) are preserved.
    """
    if resp.status_code != 200:
        return resp, None
    try:
        body = json.loads(resp.body)
    except (ValueError, TypeError):
        return resp, None
    # Fire whenever the body carries ANY secret (a service token or the preauth
    # key), so a ready payload with only a preauth key is still stripped -- the
    # "ALWAYS strip" guarantee must not hinge on a service token being present.
    if not isinstance(body, dict) or not any(body.get(k) for k in _STRIP_KEYS):
        return resp, None
    # Persist best-effort (only possible when a controller token is present);
    # never let a save error keep a secret in the body.
    if body.get("controller_token"):
        try:
            mesh_credentials.save_mesh_credentials(body)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cluster-join: could not persist mesh credentials: %s", exc)

    preauth = body.get("preauth_key") or body.get("headscale_preauth_key")
    join_intent = None
    if preauth:
        # The Headscale node hostname; the exact value taos.my routes on is the
        # host row identity, so prefer an explicit hostname field, else host_id.
        hostname = body.get("hostname") or body.get("host_id") or "taos-host"
        join_intent = {"preauth_key": preauth, "hostname": str(hostname)}

    stripped = {k: v for k, v in body.items() if k not in _STRIP_KEYS}
    out = Response(
        content=json.dumps(stripped).encode("utf-8"),
        status_code=200,
        media_type="application/json",
    )
    for raw in resp.raw_headers:
        if raw[0].decode("latin-1").lower() in ("set-cookie", "location", "cache-control"):
            out.raw_headers.append(raw)
    return out, join_intent


# Preauth keys are single-use: once a join has been attempted for one it cannot
# be retried, so we do not re-fire on every re-delivered ready poll. In-memory is
# sufficient (a process restart that loses this simply means a fresh join
# opportunity, and the key is stale by then anyway).
_attempted_preauth: set[str] = set()

# Guest preauth keys (C2 cross-user transport). Populated by
# cluster_guest_preauth when taos.my mints a guest preauth key; consumed
# by the D1 delegation-accept handler for out-of-band delivery to the guest
# instance via the peer channel.
#
# Single-use keys are stale after a short window (Headscale rejects them
# once expired), so entries auto-evict after _GUEST_PREAUTH_TTL_SECONDS.
_GUEST_PREAUTH_TTL_SECONDS = 300  # 5 min — Headscale default expiry window
_guest_preauth_intents: dict[str, tuple[float, dict]] = {}


def _pop_guest_preauth_intent(cid: str) -> dict | None:
    """Consume-and-delete a guest preauth intent. Also sweeps stale entries."""
    now = time.monotonic()
    # Sweep: delete all expired entries on any access.
    expired = [k for k, (ts, _) in _guest_preauth_intents.items() if now - ts > _GUEST_PREAUTH_TTL_SECONDS]
    for k in expired:
        _guest_preauth_intents.pop(k, None)
    entry = _guest_preauth_intents.pop(cid, None)
    if entry is None:
        return None
    return entry[1]

# Keep a strong reference to in-flight background tasks so they are not
# garbage-collected mid-run (a bare create_task() can be dropped -> "coroutine
# was never awaited").
_mesh_join_tasks: set = set()


async def _run_mesh_join(intent: dict) -> None:
    """Background task: consume the single-use preauth key to join the mesh.
    Fail-soft; a host without tailscale (or a failed join) is logged, never
    raised, so the poll it was triggered from is unaffected."""
    result = await mesh.mesh_up(intent["preauth_key"], intent["hostname"])
    if not result.get("ok"):
        logger.warning("taosgo: background mesh join did not complete: %s", result.get("detail"))


@router.get("/api/account/cluster/join/requests/{rid}/poll")
async def cluster_join_poll(request: Request, rid: str):
    if not _valid_rid(rid):
        return JSONResponse({"error": "invalid request id"}, status_code=400)
    resp = await _forward_to(request, "GET", f"{_JOIN_BASE}/requests/{rid}/poll")
    out, join_intent = _persist_join_credentials(resp)
    if (
        join_intent
        and join_intent["preauth_key"] not in _attempted_preauth
        and not await mesh.is_joined()
    ):
        # A single-use key: mark it attempted so a re-delivered ready poll does
        # not re-fire a doomed re-join. Fire-and-forget (a slow `tailscale up`
        # must never block the poll), holding a reference so it is not GC'd.
        _attempted_preauth.add(join_intent["preauth_key"])
        task = asyncio.create_task(_run_mesh_join(join_intent))
        _mesh_join_tasks.add(task)
        task.add_done_callback(_mesh_join_tasks.discard)
    return out


@router.get("/api/account/mesh/status")
async def mesh_status(request: Request):
    """Report this host's mesh membership for the Account pane
    ({joined, tailnet, node_ip, ...}). Fail-soft (never raises)."""
    return JSONResponse(await mesh.mesh_status())
