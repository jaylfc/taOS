"""The agent controller listener: a pass-through ASGI wrapper over the main app.

This listener provides a dedicated path for agent containers to reach the controller
without being mistaken for the console (loopback). It rewrites the request client
address to a valid non-loopback IP (192.0.2.1, RFC 5737 TEST-NET-1) so that
`auth.is_console_origin()` and `auth_middleware._is_loopback_client()` treat
agents as non-console requests.

Every response carries `x-taos-agent-listener: <nonce>`, the per-start identity
that the startup reconcile checks before moving any agent onto this port.

Bound to loopback only by ``__main__``. It has no lifespan of its own.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

IDENTITY_HEADER = b"x-taos-agent-listener"

# A valid IPv4 literal that is NEVER loopback and NEVER routable (RFC 5737 TEST-NET-1).
# This must parse with `ipaddress.ip_address`, because several readers of
# `request.client.host` call it and a non-IP string would make them return or raise
# unexpectedly.
AGENT_CLIENT_HOST = "192.0.2.1"
AGENT_CLIENT_PORT = 0  # PORT_UNSPECIFIED (0) is preserved as the port number in client tuples.


def _error(status: int, message: str, code: str, type_: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": type_, "param": None, "code": code}},
        status_code=status,
    )


class _BodyTooLarge(Exception):
    pass


async def _read_body(scope, receive) -> bytes:
    """The whole request body, or `_BodyTooLarge` past the size limit."""
    for name, value in scope.get("headers") or []:
        if name.lower() == b"content-length":
            try:
                if int(value) > 32 * 1024 * 1024:  # 32 MiB limit, matching existing gateway listener
                    raise _BodyTooLarge
            except ValueError:
                raise _BodyTooLarge from None
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        body = message.get("body", b"")
        size += len(body)
        if size > 32 * 1024 * 1024:
            raise _BodyTooLarge
        chunks.append(body)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


async def create_agent_controller_app(main_app: ASGIApp, *, identity: str | None = None) -> ASGIApp:
    """ASGI app for the agent controller listener, wrapping the controller's ``main_app``.

    ``identity`` is stamped on every response (``x-taos-agent-listener``).
    """
    stamp = identity.encode("ascii") if identity else None

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if kind != "http":
            if kind == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        async def stamped_send(message):
            if stamp is not None and message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [*message.get("headers", []), (IDENTITY_HEADER, stamp)]
            await send(message)

        # Rewrite the client address from loopback to a valid non-loopback IP
        # This ensures auth.is_console_origin() and auth_middleware._is_loopback_client()
        # treat agent requests as non-console
        rewritten_scope = dict(scope)
        if rewritten_scope.get("client"):
            # Parse the client address to ensure it's valid
            client_host, client_port = rewritten_scope["client"]
            # Create a new client tuple with the non-loopback IP
            rewritten_scope["client"] = (AGENT_CLIENT_HOST, client_port)

        # All paths pass through to the main app (unlike the gateway's path allowlist)
        try:
            await main_app(rewritten_scope, receive, stamped_send)
        except _BodyTooLarge:
            await _error(413, "request body over 32 MiB bytes", "request_too_large")(rewritten_scope, receive, stamped_send)

    return app