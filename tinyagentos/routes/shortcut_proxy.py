"""Worker /redeem endpoint — validates HMAC ticket, sets cookie, 302.

Also contains the shortcut dashboard reverse-proxy route:
  GET  /shortcut/dashboard/{agent_name}/{idx}/{path:path}

WebSocket proxy is added in the next task.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from http.cookies import SimpleCookie
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import RedirectResponse, StreamingResponse

from tinyagentos.shortcuts.tickets import validate_ticket, _GLOBAL_JTI_TRACKER
from tinyagentos.cluster.worker_registry import get_local_worker

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Session store (shared between /redeem and proxy routes)
# ---------------------------------------------------------------------------

# In-memory session store: session_id -> {agent_id, shortcut_idx, scope, expires_at}
_sessions: dict[str, dict[str, Any]] = {}
_SESSION_IDLE_TTL = 300  # 5 minutes


def _new_session(agent_id: str, shortcut_idx: int, scope: str) -> str:
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "agent_id": agent_id,
        "shortcut_idx": shortcut_idx,
        "scope": scope,
        "expires_at": time.monotonic() + _SESSION_IDLE_TTL,
    }
    return session_id


def _get_session(session_id: str) -> dict[str, Any]:
    """Return session or raise HTTPException(401)."""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Session not found or expired")
    if time.monotonic() > session["expires_at"]:
        del _sessions[session_id]
        raise HTTPException(status_code=401, detail="Session expired")
    session["expires_at"] = time.monotonic() + _SESSION_IDLE_TTL
    return session


def _scope_to_path(scope: str, agent_id: str, shortcut_idx: int) -> str:
    if scope == "dashboard":
        return f"/shortcut/dashboard/{agent_id}/{shortcut_idx}/"
    return f"/shortcut/terminal/{agent_id}/{shortcut_idx}"


# ---------------------------------------------------------------------------
# Proxy helpers — Task 17: basic HTTP forward
# ---------------------------------------------------------------------------

# Hop-by-hop headers that must not be forwarded (RFC 2616 §13.5.1).
_HOP_BY_HOP_PROXY = frozenset({
    "connection",
    "keep-alive",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
})

# Controller cookies that must not leak to upstream containers.
_STRIPPED_PROXY_COOKIES = frozenset({"taos_session", "taos_shortcut"})


def _filter_proxy_headers(headers: dict[str, str]) -> dict[str, str]:
    """Strip hop-by-hop and sensitive cookies; return a clean header dict."""
    filtered: dict[str, str] = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in _HOP_BY_HOP_PROXY:
            continue
        if kl == "cookie":
            jar = SimpleCookie()
            try:
                jar.load(v)
            except Exception:
                filtered[k] = v
                continue
            for name in _STRIPPED_PROXY_COOKIES:
                jar.pop(name, None)
            stripped = "; ".join(f"{ck}={m.value}" for ck, m in jar.items())
            if stripped:
                filtered[k] = stripped
            # If all cookies were controller-owned, drop the header entirely.
            continue
        filtered[k] = v
    return filtered


def _resolve_container_ip(request: Request, agent_name: str) -> Optional[str]:
    """Return the container IP for *agent_name*, or None if not found.

    Looks up the agent in app.state.config.agents by id OR name.
    The 'host' field stores the IP set at deploy time.
    """
    agents = getattr(request.app.state.config, "agents", [])
    for agent in agents:
        if agent.get("id") == agent_name or agent.get("name") == agent_name:
            return agent.get("host") or None
    return None


def _get_shortcuts_for_agent(request: Request, agent_name: str) -> list[dict[str, Any]]:
    """Return the framework shortcuts list for agent_name, or []."""
    from tinyagentos.frameworks import FRAMEWORKS
    agents = getattr(request.app.state.config, "agents", [])
    for agent in agents:
        if agent.get("id") == agent_name or agent.get("name") == agent_name:
            framework = FRAMEWORKS.get(agent.get("framework", ""), {})
            return framework.get("shortcuts", [])
    return []


def _get_shortcut_from_cookie(
    connection: Any,
    agent_name: str,
    idx: int,
    shortcuts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the taos_shortcut cookie and return the shortcut dict.

    Raises HTTPException:
      401 — missing/expired session
      403 — session doesn't match agent_name or idx
      404 — shortcut idx out of range
    """
    session_id = connection.cookies.get("taos_shortcut")
    if not session_id:
        raise HTTPException(status_code=401, detail="No shortcut session cookie")

    session = _get_session(session_id)  # raises 401 if missing/expired

    if session["agent_id"] != agent_name:
        raise HTTPException(status_code=403, detail="Session agent mismatch")

    if session["shortcut_idx"] != idx:
        raise HTTPException(status_code=403, detail="Session shortcut index mismatch")

    if idx < 0 or idx >= len(shortcuts):
        raise HTTPException(status_code=404, detail=f"Shortcut idx {idx} not found")

    return shortcuts[idx]


# Module-level async HTTP client — reused across requests.
_proxy_client = httpx.AsyncClient(timeout=60.0)


async def _build_auth_header(
    agent_name: str,
    shortcut: dict[str, Any],
) -> Optional[tuple[str, str]]:
    """Return (header_name, header_value) for the shortcut's auth config, or None.

    Reads the token via read_token_source (sync) wrapped in asyncio.to_thread
    so the event loop is not blocked.
    """
    auth = shortcut.get("auth") or {}
    auth_type = auth.get("type", "none")
    if auth_type == "none":
        return None

    token_source = auth.get("token_source")
    if not token_source:
        return None

    from tinyagentos.shortcuts.token_source import read_token_source
    token = await asyncio.to_thread(read_token_source, agent_name, token_source)
    if not token:
        return None

    if auth_type == "bearer":
        return ("Authorization", f"Bearer {token}")
    if auth_type == "basic":
        encoded = base64.b64encode(token.encode()).decode()
        return ("Authorization", f"Basic {encoded}")

    return None


async def proxy_dashboard(
    agent_name: str,
    shortcut: dict[str, Any],
    request: Request,
) -> Any:
    """Forward the HTTP request to the agent's container dashboard port.

    Resolves container IP, strips hop-by-hop headers, and streams the
    upstream response back to the client.
    """
    ip = _resolve_container_ip(request, agent_name)
    if ip is None:
        return _json_error(
            f"No container IP for agent '{agent_name}' — is the container running?",
            503,
        )

    port = shortcut["port"]
    base_path = (shortcut.get("path") or "/").rstrip("/")
    upstream_path = base_path + "/"

    upstream_url = f"http://{ip}:{port}{upstream_path}"
    query = request.url.query
    if query:
        upstream_url = f"{upstream_url}?{query}"

    fwd_headers = _filter_proxy_headers(dict(request.headers))

    # Inject auth header if configured.
    auth_header = await _build_auth_header(agent_name, shortcut)
    if auth_header:
        fwd_headers[auth_header[0]] = auth_header[1]

    async def _stream_body():
        async for chunk in request.stream():
            yield chunk

    try:
        req = _proxy_client.build_request(
            method=request.method,
            url=upstream_url,
            headers=fwd_headers,
            content=_stream_body(),
        )
        upstream_resp = await _proxy_client.send(req, stream=True, follow_redirects=False)
    except httpx.ConnectError as exc:
        return _json_error(
            f"Cannot reach agent '{agent_name}' dashboard at {ip}:{port}: {exc}",
            502,
        )
    except httpx.TimeoutException:
        return _json_error(
            f"Agent '{agent_name}' dashboard at {ip}:{port} timed out",
            504,
        )

    resp_headers = _filter_proxy_headers(dict(upstream_resp.headers))

    from starlette.background import BackgroundTask
    return StreamingResponse(
        upstream_resp.aiter_bytes(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream_resp.aclose),
    )


def _json_error(message: str, status_code: int):
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": message}, status_code=status_code)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/redeem")
async def redeem_ticket(
    t: str = Query(..., description="Base64url-encoded HMAC ticket"),
) -> RedirectResponse:
    """Validate ticket, set session cookie, redirect to the shortcut endpoint."""
    worker = get_local_worker()
    signing_key: bytes = worker["signing_key"]

    try:
        ticket = validate_ticket(t, signing_key=signing_key, tracker=_GLOBAL_JTI_TRACKER)
    except ValueError as exc:
        msg = str(exc)
        if "expired" in msg:
            detail = "ticket expired"
        elif "replay" in msg.lower() or "replayed" in msg.lower():
            detail = "replay detected"
        else:
            detail = "invalid ticket"
        raise HTTPException(status_code=401, detail=detail) from exc

    session_id = _new_session(
        agent_id=ticket.agent_id,
        shortcut_idx=ticket.shortcut_idx,
        scope=ticket.scope,
    )
    location = _scope_to_path(ticket.scope, ticket.agent_id, ticket.shortcut_idx)

    response = RedirectResponse(url=location, status_code=302)
    response.set_cookie(
        key="taos_shortcut",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=_SESSION_IDLE_TTL,
        path="/",
    )
    return response


@router.api_route(
    "/shortcut/dashboard/{agent_name}/{idx}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def shortcut_dashboard_proxy(
    agent_name: str,
    idx: int,
    path: str,
    request: Request,
):
    """Reverse-proxy for shortcut dashboards.

    Validates the taos_shortcut cookie, resolves the container IP,
    then forwards the request to the container port.
    """
    shortcuts = _get_shortcuts_for_agent(request, agent_name)
    shortcut = _get_shortcut_from_cookie(request, agent_name, idx, shortcuts)
    shortcut = {**shortcut, "_idx": idx}

    return await proxy_dashboard(agent_name, shortcut, request)
