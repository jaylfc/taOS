"""Cap the request body of the upload endpoints while it is still arriving.

A route that declares ``package: UploadFile = File(...)`` never gets to decide
how much it accepts. FastAPI resolves that parameter by calling
``await request.form()`` *before* the handler runs, and Starlette's multipart
parser spools a file part to a ``SpooledTemporaryFile`` with no size limit of
its own -- ``max_part_size`` is only consulted for parts without a filename
(``starlette/formparsers.py``, ``MultiPartParser.on_part_data``). So a handler
reading ``cap + 1`` bytes answers 413 truthfully but far too late: the hostile
body has already been written to temporary storage.

This middleware is the half that has to run earlier. It is plain ASGI (not
``BaseHTTPMiddleware``) so it can wrap ``receive`` itself, and it is added last
in ``create_app`` so it wraps everything else and the cap is in place before any
downstream layer pulls a byte of the body.

Each capped endpoint registers itself with :func:`register_upload_cap`, passing
a callable rather than a number so the route's own constant stays the single
definition of the limit -- the handler's ``read(cap + 1)`` check remains as
defence in depth, and both move together.
"""

from __future__ import annotations

from collections.abc import Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# path -> callable returning the cap in bytes, read fresh on every request.
UPLOAD_BODY_CAPS: dict[str, Callable[[], int]] = {}


def register_upload_cap(path: str, get_cap: Callable[[], int]) -> None:
    """Declare the request-body cap for one upload endpoint."""
    UPLOAD_BODY_CAPS[path] = get_cap


class UploadBodyLimitMiddleware:
    """Refuse an over-cap body before the multipart parser can store it."""

    def __init__(self, app: ASGIApp, caps: dict[str, Callable[[], int]] | None = None):
        self.app = app
        # Bound by reference, so a route registering later (or a test patching
        # an entry) is picked up without rebuilding the middleware stack.
        self.caps = UPLOAD_BODY_CAPS if caps is None else caps

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        get_cap = self.caps.get(scope.get("path", ""))
        if get_cap is None:
            await self.app(scope, receive, send)
            return
        cap = get_cap()

        # A declared Content-Length is the cheap case: refuse without reading.
        # It is only a hint -- a chunked body carries none, and a lying one is
        # still bounded by the running count below.
        declared = Headers(scope=scope).get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            await self._too_large(cap, scope, send)
            return

        received = 0
        started = False
        refused = False

        async def limited_receive() -> Message:
            """Feed the body through, and hang up the moment it overruns.

            Answering here rather than raising is deliberate: FastAPI wraps
            form parsing in a bare ``except Exception`` and would turn a raised
            sentinel into its generic 400 "error parsing the body". Reporting a
            disconnect instead unwinds the parser through a path it already
            handles, while the 413 this middleware just sent is the response
            that reaches the client.
            """
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap and not started:
                    refused = True
                    await self._too_large(cap, scope, send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # Once the 413 is out the connection is ours; whatever the app
            # produces for the truncated body it never finished reading is
            # dropped rather than appended to a response already sent.
            if refused:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    @staticmethod
    async def _too_large(cap: int, scope: Scope, send: Send) -> None:
        response = JSONResponse(
            {"error": f"request body too large (max {cap} bytes)"}, status_code=413
        )
        # Response.__call__ never pulls from receive; the stub keeps the
        # signature honest without handing it a channel we have finished with.
        await response(scope, _closed_receive, send)


async def _closed_receive() -> Message:  # pragma: no cover - never awaited
    return {"type": "http.disconnect"}
