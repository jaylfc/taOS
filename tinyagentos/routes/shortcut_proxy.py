"""Worker /redeem endpoint — validates HMAC ticket, sets cookie, 302.

Also contains the shortcut reverse-proxy routes:
  GET  /shortcut/dashboard/{agent_name}/{idx}/{path:path}
  WS   /shortcut/terminal/{agent_name}/{idx}
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import shlex
import shutil
import time
from http.cookies import SimpleCookie
from typing import Any, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket
from fastapi.responses import RedirectResponse, StreamingResponse

from tinyagentos.cluster.worker_registry import get_local_worker
from tinyagentos.issued_cookies import TAOS_ISSUED_COOKIES
from tinyagentos.shortcuts.tickets import _GLOBAL_JTI_TRACKER, validate_ticket

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Session store (shared between /redeem and proxy routes)
# ---------------------------------------------------------------------------

# In-memory session store: session_id -> {agent_id, shortcut_idx, scope, expires_at}
#
# Known limitation: this store is process-local. In a multi-worker (multi-process)
# ASGI deployment, /redeem and the follow-up /shortcut/... request must hit the same
# worker process, otherwise the session lookup returns 401. Sticky sessions on the
# load balancer (by IP or cookie) are required for that deployment model.
# This is intentional for v1 — a shared backend (Redis/DB) would solve it but
# introduces a hard dependency not warranted until horizontal scale is needed.
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

# Controller cookies that must not leak to upstream containers. Imported from
# the one shared set rather than restated here: this list named two of the five
# cookies taOS issues, and the other three leaked.
_STRIPPED_PROXY_COOKIES = TAOS_ISSUED_COOKIES


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


_TERMINAL_SCOPES = frozenset({"container-terminal", "tui"})


def _get_shortcut_from_cookie(
    connection: Any,
    agent_name: str,
    idx: int,
    shortcuts: list[dict[str, Any]],
    expected_scope: Optional[str] = None,
) -> dict[str, Any]:
    """Validate the taos_shortcut cookie and return the shortcut dict.

    Raises HTTPException:
      401 — missing/expired session
      403 — session doesn't match agent_name, idx, or scope
      404 — shortcut idx out of range

    expected_scope:
      "dashboard" — session scope must be "dashboard"
      "terminal"  — session scope must be in {"container-terminal", "tui"}
      None        — scope not checked (backwards-compat)
    """
    session_id = connection.cookies.get("taos_shortcut")
    if not session_id:
        raise HTTPException(status_code=401, detail="No shortcut session cookie")

    session = _get_session(session_id)  # raises 401 if missing/expired

    if session["agent_id"] != agent_name:
        raise HTTPException(status_code=403, detail="Session agent mismatch")

    if session["shortcut_idx"] != idx:
        raise HTTPException(status_code=403, detail="Session shortcut index mismatch")

    if expected_scope == "dashboard" and session["scope"] != "dashboard":
        raise HTTPException(status_code=403, detail="Session scope mismatch")
    if expected_scope == "terminal" and session["scope"] not in _TERMINAL_SCOPES:
        raise HTTPException(status_code=403, detail="Session scope mismatch")

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
    path: str = "",
) -> Any:
    """Forward the HTTP request to the agent's container dashboard port.

    Resolves container IP, strips hop-by-hop headers, and streams the
    upstream response back to the client.

    path is the captured route segment (no leading slash). When provided it is
    appended to the shortcut's base path so deep links work correctly.
    """
    ip = _resolve_container_ip(request, agent_name)
    if ip is None:
        return _json_error(
            f"No container IP for agent '{agent_name}' — is the container running?",
            503,
        )

    port = shortcut["port"]
    base_path = (shortcut.get("path") or "/").rstrip("/")
    upstream_path = f"{base_path}/{path}" if path else f"{base_path}/"

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
    try:
        worker = get_local_worker()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Worker not ready: {exc}",
        ) from exc
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
    shortcut = _get_shortcut_from_cookie(request, agent_name, idx, shortcuts, expected_scope="dashboard")
    shortcut = {**shortcut, "_idx": idx}

    return await proxy_dashboard(agent_name, shortcut, request, path=path)


# ---------------------------------------------------------------------------
# Terminal PTY bridge helpers — Task 20 / 21
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Persistent terminal sessions (#617)
#
# A terminal shortcut's shell/TUI runs inside a tmux session in the agent's
# container, named taos-term-<agent>-<kind>-<idx> (one session per shortcut
# window). The WebSocket attaches-or-creates; closing the socket detaches and
# the session keeps running. The lifecycle routes in
# routes/agent_terminals.py list/restart/kill these sessions and share the
# naming helpers below.
# ---------------------------------------------------------------------------

_TMUX_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def _tmux_safe(part: str) -> str:
    """Make *part* safe for a tmux session name ('.' and ':' are target syntax)."""
    return _TMUX_UNSAFE_CHARS.sub("-", str(part)).strip("-") or "x"


def terminal_session_name(agent_name: str, kind: str, idx: int) -> str:
    """tmux session name for agent_name's terminal shortcut window *idx*."""
    return f"taos-term-{_tmux_safe(agent_name)}-{_tmux_safe(kind)}-{idx}"


def parse_terminal_session(agent_name: str, session: str) -> tuple[str, int] | None:
    """Return (kind, idx) if *session* is agent_name's terminal session, else None."""
    prefix = f"taos-term-{_tmux_safe(agent_name)}-"
    if not session.startswith(prefix):
        return None
    kind, sep, idx = session[len(prefix):].rpartition("-")
    if not sep or not kind or not idx.isdigit():
        return None
    return kind, int(idx)


def tmux_session_cmd(
    session_name: str, inner_cmd: list[str] | None, *, detached: bool = False
) -> list[str]:
    """Build the tmux new-session command for a terminal session.

    Attached form (the WebSocket spawn): ``tmux new-session -A -s <name>``
    attaches if the session exists, else creates it. Detached form (the
    restart route): ``-d`` instead of ``-A``. *inner_cmd* (a tui shortcut's
    command) is appended as ONE shell-command string so its quoting survives
    tmux's sh -c handling; None starts the session's default shell.
    """
    cmd = ["tmux", "new-session", "-d" if detached else "-A", "-s", session_name]
    if inner_cmd:
        cmd.append(" ".join(shlex.quote(c) for c in inner_cmd))
    return cmd


# One-line notice shown in the terminal when the container image has no
# tmux (#617 follow-up): the terminal still opens, it just runs the plain
# pre-#617 spawn and dies with the window.
TMUX_ABSENT_NOTICE = (
    "tmux is not installed in this container — this terminal won't "
    "persist when the window closes.\r\n"
)


async def _tmux_available(agent_name: str) -> bool:
    """True when the terminal spawn for *agent_name* can use tmux.

    Container backends: probe inside the agent's container. The native
    backend runs the PTY on the host, so probe the host instead. Any probe
    error reads as unavailable — the caller falls back to a plain
    terminal, which must never fail to open just because the probe could
    not run.
    """
    from tinyagentos.containers import exec_in_container
    from tinyagentos.containers.backend import get_backend
    from tinyagentos.containers.lxc import LXCBackend
    from tinyagentos.containers.native import NativeBackend

    try:
        backend = get_backend()
    except Exception:
        return False
    if isinstance(backend, NativeBackend):
        return shutil.which("tmux") is not None
    try:
        if isinstance(backend, LXCBackend):
            # The module-level exec resolves the container's incus project
            # before exec'ing; the LXC backend's own exec does not, so keep
            # the module path for Incus.
            code, _output = await exec_in_container(
                f"taos-agent-{agent_name}",
                ["sh", "-c", "command -v tmux >/dev/null 2>&1"],
                timeout=10,
            )
        else:
            # Probe through the ACTIVE backend: the module-level
            # exec_in_container is hard-coded to `incus exec`, so on a
            # Docker/Apple host it would fail and wrongly report tmux
            # absent, degrading every terminal to the plain fallback.
            code, _output = await backend.exec_in_container(
                f"taos-agent-{agent_name}",
                ["sh", "-c", "command -v tmux >/dev/null 2>&1"],
                timeout=10,
            )
    except Exception:
        return False
    return code == 0


def _get_container_pty(agent_name: str, cmd: list[str] | None):
    """Spawn a PTY inside the agent's container.

    Delegates to the active ContainerBackend.spawn_pty.  cmd=None → default
    shell; cmd=['bash','-lc','<command>'] → tui shortcut.
    """
    from tinyagentos.containers.backend import get_backend
    backend = get_backend()
    return backend.spawn_pty(agent_name, cmd)


async def _await_to_completion(coro) -> None:
    """Await a cleanup coroutine to completion even if this task is
    cancelled again while waiting (same pattern as
    routes/agent_auth_requests._await_cleanup): shield alone returns
    control on a second cancellation while the cleanup runs detached.
    Cleanup failures are dropped here; the cleanup steps log their own
    failures and must never mask the handler's outcome."""
    task = asyncio.ensure_future(coro)
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            break
    if task.done() and not task.cancelled():
        task.exception()


@router.websocket("/shortcut/terminal/{agent_name}/{idx}")
async def shortcut_terminal_ws(
    agent_name: str,
    idx: int,
    websocket: WebSocket,
):
    """WebSocket PTY bridge for container-terminal and tui shortcuts.

    Validates the taos_shortcut cookie, opens a PTY inside the agent container
    via the active ContainerBackend, then pipes data in both directions until
    either end closes.
    """
    shortcuts = _get_shortcuts_for_agent(websocket, agent_name)
    try:
        shortcut = _get_shortcut_from_cookie(websocket, agent_name, idx, shortcuts, expected_scope="terminal")
    except HTTPException as exc:
        await websocket.close(code=1008, reason=exc.detail)
        return

    scope = shortcut["kind"]
    if scope not in ("container-terminal", "tui"):
        await websocket.close(code=1008, reason=f"Unsupported shortcut kind: {scope}")
        return

    # For tui shortcuts pass the configured command; for container-terminal use
    # the default shell (inner cmd=None).
    if scope == "tui":
        command = shortcut.get("command", "")
        inner_cmd: list[str] | None = ["bash", "-lc", command]
    else:
        inner_cmd = None

    await websocket.accept()

    # Validate handshake BEFORE spawning PTY — prevents resource leak when a
    # client with a valid cookie sends a bad or missing first frame.
    # The ticket was already consumed at /redeem to mint the cookie session;
    # we don't re-validate it cryptographically here (the cookie is the
    # authoritative auth). Requiring this frame confirms the client intends a
    # shortcut connection (defense-in-depth per Task 29 protocol spec).
    try:
        first_frame = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        handshake = json.loads(first_frame)
        if handshake.get("type") != "ticket" or not handshake.get("ticket"):
            await websocket.close(code=1008, reason="malformed ticket handshake")
            return
    except (json.JSONDecodeError, KeyError, asyncio.TimeoutError) as exc:
        await websocket.close(code=1008, reason=f"invalid handshake: {exc}")
        return

    # #617: with tmux in the container, run the shell/TUI inside a
    # persistent tmux session — attach if it already exists, create it
    # otherwise; closing this WebSocket then detaches instead of killing
    # the shell, and reopening reattaches. Without tmux (image not yet
    # rebuilt with it), fall back to the plain pre-#617 spawn plus a
    # one-line notice — a terminal must never fail to open just because
    # persistence is unavailable.
    if await _tmux_available(agent_name):
        cmd: list[str] | None = tmux_session_cmd(
            terminal_session_name(agent_name, scope, idx), inner_cmd
        )
        notice: str | None = None
    else:
        cmd = inner_cmd
        notice = TMUX_ABSENT_NOTICE

    try:
        pty_handle = await asyncio.to_thread(_get_container_pty, agent_name, cmd)
    except RuntimeError as exc:
        logger.warning("terminal: no container backend for %s: %s", agent_name, exc)
        await websocket.close(
            code=1011,
            reason="No container backend available. Install Incus or Docker and restart taOS.",
        )
        return
    except Exception as exc:
        logger.warning("terminal: spawn_pty failed for %s: %s", agent_name, exc)
        await websocket.close(code=1011, reason="PTY spawn failed")
        return

    async def pty_to_ws():
        try:
            while True:
                data = await asyncio.to_thread(pty_handle.read)
                if not data:
                    await asyncio.sleep(0.02)
                    continue
                await websocket.send_text(data.decode("utf-8", errors="replace"))
        except Exception:
            pass

    async def ws_to_pty():
        try:
            while True:
                msg = await websocket.receive_text()
                await asyncio.to_thread(pty_handle.write, msg.encode("utf-8"))
        except Exception:
            pass

    reader = asyncio.ensure_future(pty_to_ws())
    writer = asyncio.ensure_future(ws_to_pty())
    try:
        # Inside the cleanup try: if the client has already gone away this
        # send raises, and the finally below still closes the PTY handle.
        if notice is not None:
            await websocket.send_text(notice)
        # First finisher wins: pty_to_ws has no exit condition for a quiet
        # shell (read() returns b"" and it loops), so a bare gather would
        # keep waiting on it after ws_to_pty returns on client disconnect
        # and the finally below would never close the PTY.
        await asyncio.wait(
            {reader, writer}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        # Deterministic close: cancel whichever direction is still
        # running, AWAIT its cancellation, then close the PTY handle in
        # this finally, shielded via _await_to_completion. The disconnect
        # that ends the bridge can also cancel this handler task, and an
        # unshielded await in the finally is then aborted before the
        # close runs, leaking the PTY (the 3.13 shard-1 failure of
        # test_disconnect_with_quiet_shell_closes_pty on #4576).
        for task in (reader, writer):
            task.cancel()
        await _await_to_completion(
            asyncio.gather(reader, writer, return_exceptions=True)
        )
        await _await_to_completion(asyncio.to_thread(pty_handle.close))
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/shortcut/dashboard/{agent_name}/{idx}/ws/{path:path}")
async def shortcut_dashboard_ws(
    agent_name: str,
    idx: int,
    path: str,
    websocket: WebSocket,
):
    """WebSocket proxy for shortcut dashboards.

    Validates the taos_shortcut cookie, then upgrades and pipes both
    directions to the container's dashboard WebSocket endpoint.
    """
    shortcuts = _get_shortcuts_for_agent(websocket, agent_name)
    try:
        shortcut = _get_shortcut_from_cookie(websocket, agent_name, idx, shortcuts, expected_scope="dashboard")
    except HTTPException as exc:
        await websocket.close(code=1008, reason=exc.detail)
        return

    ip = _resolve_container_ip(websocket, agent_name)
    if ip is None:
        await websocket.close(code=1011, reason="No container IP")
        return

    port = shortcut["port"]
    base_path = (shortcut.get("path") or "/").rstrip("/")
    ws_path = f"{base_path}/ws/{path}" if path else f"{base_path}/ws/"
    upstream_ws_url = f"ws://{ip}:{port}{ws_path}"

    # Build auth header for upstream, same as HTTP proxy does.
    auth_header = await _build_auth_header(agent_name, shortcut)
    extra_headers: dict[str, str] = {}
    if auth_header:
        extra_headers[auth_header[0]] = auth_header[1]

    await websocket.accept()

    try:
        import websockets as _ws_lib
        async with _ws_lib.connect(
            upstream_ws_url,
            additional_headers=extra_headers,
        ) as upstream:
            async def _client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                except Exception:
                    pass

            async def _upstream_to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(
                _client_to_upstream(),
                _upstream_to_client(),
                return_exceptions=True,
            )
    except Exception as exc:
        logger.debug("WS proxy error for %s: %s", agent_name, exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
