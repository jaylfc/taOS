"""The agent listener: the gateway at the base URL agents already use.

Agents call ``http://127.0.0.1:4000/v1/...`` (openclaw: ``.../chat/completions``
with no ``/v1``). Once the cutover retargets their proxy device, those
requests land here, on the host's ``127.0.0.1:<agent port>`` (7838), not on
the controller's main port: there ``/v1/chat/completions`` is Agent-as-a-Model,
a different API.

An ALLOWLIST, nothing else:

- ``/v1/models``, ``/v1/chat/completions`` and ``/v1/embeddings`` (and the
  un-prefixed forms) are rewritten to ``/api/llm/v1/...`` and handed to the
  MAIN app object, so the auth middleware exemptions and ``gateway_caller``
  (keys, allowlists, budgets) run exactly as for any other gateway call.
  Embeddings (``TAOS_EMBEDDING_URL``) are served by the gateway itself.
- Everything else is a 404: LiteLLM's admin API (``/key/generate``,
  ``/model/new``, ``/config/update``, ...), ``/v1/messages`` and
  ``/v1/responses`` (which would skip the gateway's checks), and every
  controller route.

Paths are normalised first (trailing slashes dropped; empty, ``.`` and ``..``
segments refused), so ``/v1/chat/completions/`` cannot route around the
gateway. Request bodies are bounded (``MAX_BODY_BYTES``, 413 beyond it).

Every response carries ``x-taos-llm-listener: <nonce>``, the per-start
identity the startup reconcile checks before moving any agent here (so a
different process that grabbed the port is never mistaken for this one).

Bound to loopback only by ``__main__``. It has no lifespan of its own.
"""
from __future__ import annotations

import logging

from starlette.responses import JSONResponse

from tinyagentos.llm_gateway.router import PREFIX

logger = logging.getLogger(__name__)

IDENTITY_HEADER = b"x-taos-llm-listener"

GATEWAY_PATHS = {
    "/v1/models": f"{PREFIX}/models",
    "/models": f"{PREFIX}/models",
    "/v1/chat/completions": f"{PREFIX}/chat/completions",
    "/chat/completions": f"{PREFIX}/chat/completions",
    "/v1/embeddings": f"{PREFIX}/embeddings",
    "/embeddings": f"{PREFIX}/embeddings",
}
# Generous for chat with inline images, far below anything that hurts an SBC.
MAX_BODY_BYTES = 32 * 1024 * 1024



def _error(status: int, message: str, code: str, type_: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": type_, "param": None, "code": code}},
        status_code=status,
    )


def normalise_path(path: str) -> str | None:
    """The canonical form of ``path``, or None when it is not a plain path."""
    if not path.startswith("/"):
        return None
    trimmed = path.rstrip("/")
    if not trimmed:
        return None
    segments = trimmed[1:].split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return None
    return trimmed


class _BodyTooLarge(Exception):
    pass


async def _read_body(scope, receive) -> bytes:
    """The whole request body, or ``_BodyTooLarge`` past ``MAX_BODY_BYTES``."""
    for name, value in scope.get("headers") or []:
        if name.lower() == b"content-length":
            try:
                if int(value) > MAX_BODY_BYTES:
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
        if size > MAX_BODY_BYTES:
            raise _BodyTooLarge
        chunks.append(body)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _replay(body: bytes, receive):
    """A receive() that yields the buffered body once, then defers to the
    real one (so a client disconnect still reaches a streaming response)."""
    sent = False

    async def replay():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def create_agent_listener_app(main_app, *, identity: str | None = None):
    """ASGI app for the agent listener, wrapping the controller's ``main_app``.

    ``identity`` is stamped on every response (``x-taos-llm-listener``).
    """
    stamp = identity.encode("ascii") if identity else None

    async def app(scope, receive, send):
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

        path = normalise_path(scope.get("path", ""))
        target = GATEWAY_PATHS.get(path) if path else None
        if target is None:
            await _error(404, "not found", "not_found")(scope, receive, stamped_send)
            return
        try:
            body = await _read_body(scope, receive)
        except _BodyTooLarge:
            await _error(413, f"request body over {MAX_BODY_BYTES} bytes",
                         "request_too_large")(scope, receive, stamped_send)
            return
        rewritten = dict(scope)
        rewritten["path"] = target
        rewritten["raw_path"] = target.encode("ascii")
        await main_app(rewritten, _replay(body, receive), stamped_send)

    return app
