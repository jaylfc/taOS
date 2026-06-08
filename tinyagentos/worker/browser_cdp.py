from __future__ import annotations

"""Minimal async CDP (Chrome DevTools Protocol) client for the taOS browser.

Connects to a Chromium CDP endpoint reachable at ``ws://127.0.0.1:{port}``
— the host-published CDP port from the taos-neko-cdp container — and drives
native browser automation via the CDP JSON-RPC protocol.

Only ``websockets`` (already a project dependency) is used; no heavy
browser-automation SDK is pulled in.

Usage
-----
::

    async with BrowserCDP(cdp_host_port=9222) as cdp:
        await cdp.set_mobile_emulation(390, 844, "Mozilla/5.0 (iPhone ...)")
        await cdp.navigate("https://example.com")
        await cdp.dispatch_touch(
            [{"x": 200, "y": 400}], type="touchStart"
        )

Security note
-------------
CDP is accessed on the HOST's loopback only (``127.0.0.1``).  The taOS
launcher publishes the container's port 9222 with ``-p 127.0.0.1:{port}:9222``
— never ``0.0.0.0`` and never exposed over Tailscale.  The Neko WebRTC
*stream* is what crosses the network; CDP stays host-local.
"""

import asyncio
import base64
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

# Default timeouts
_CONNECT_TIMEOUT = 5.0   # seconds waiting for the WebSocket handshake
_CMD_TIMEOUT = 10.0      # seconds waiting for a CDP response


class CDPError(Exception):
    """Raised when a CDP command returns an error or the connection fails."""


class BrowserCDP:
    """Async CDP client for a single Chromium page session.

    Connects to the flat-session WebSocket endpoint obtained from
    ``/json/list`` on the CDP HTTP port.  Chromium >=148 with
    ``DeveloperToolsAvailability=0`` is required (enforced by the
    ``taos-neko-cdp`` image).

    Parameters
    ----------
    cdp_host_port:
        The host-side port that maps to the container's 9222 — e.g. the
        ``cdp_host_port`` returned by ``build_neko_run_args``.
    connect_timeout:
        Seconds to wait for the WebSocket connection to the CDP endpoint.
    cmd_timeout:
        Seconds to wait for a CDP command response.
    """

    def __init__(
        self,
        cdp_host_port: int,
        *,
        connect_timeout: float = _CONNECT_TIMEOUT,
        cmd_timeout: float = _CMD_TIMEOUT,
    ) -> None:
        self._port = cdp_host_port
        self._connect_timeout = connect_timeout
        self._cmd_timeout = cmd_timeout

        self._ws: Any = None                # websockets ClientConnection
        self._next_id: int = 1
        self._pending: dict[int, asyncio.Future[dict]] = {}
        self._recv_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BrowserCDP":
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to the CDP endpoint and attach to a page target.

        Fetches ``/json/list`` over HTTP to discover the page WebSocket URL,
        then opens the WebSocket and starts the receive loop.

        Raises ``CDPError`` if CDP is unreachable or no page target is found.
        """
        ws_url = await self._discover_page_ws()
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(ws_url, open_timeout=self._connect_timeout),
                timeout=self._connect_timeout + 1,
            )
        except Exception as exc:
            raise CDPError(f"CDP WebSocket connect failed ({ws_url}): {exc}") from exc
        self._recv_task = asyncio.create_task(self._recv_loop(), name="cdp-recv")

    async def close(self) -> None:
        """Close the CDP WebSocket and cancel the receive loop."""
        if self._recv_task is not None:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass
            self._recv_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        # Fail any still-pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CDPError("CDP connection closed"))
        self._pending.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def navigate(self, url: str) -> dict:
        """Navigate the page to ``url`` (``Page.navigate``).

        Returns the CDP response dict.
        """
        return await self._send("Page.navigate", {"url": url})

    async def dispatch_touch(
        self,
        touches: list[dict],
        *,
        type: str,  # noqa: A002  (mirrors CDP param name)
    ) -> dict:
        """Dispatch a native touch event via ``Input.dispatchTouchEvent``.

        Parameters
        ----------
        touches:
            List of touch points.  Each dict must have ``x`` and ``y`` (float,
            viewport-space pixels).  Optional keys: ``id`` (int, defaults to
            index), ``radiusX``, ``radiusY``, ``rotationAngle``, ``force``.
        type:
            One of ``touchStart``, ``touchMove``, ``touchEnd``,
            ``touchCancel``.

        Returns the CDP response dict.
        """
        if type not in ("touchStart", "touchMove", "touchEnd", "touchCancel"):
            raise ValueError(f"dispatch_touch: invalid type {type!r}")

        touch_points = []
        for i, t in enumerate(touches):
            tp: dict[str, Any] = {
                "x": float(t["x"]),
                "y": float(t["y"]),
                "id": int(t.get("id", i)),
                "radiusX": float(t.get("radiusX", 1.0)),
                "radiusY": float(t.get("radiusY", 1.0)),
                "rotationAngle": float(t.get("rotationAngle", 0.0)),
                "force": float(t.get("force", 1.0)),
            }
            touch_points.append(tp)

        return await self._send(
            "Input.dispatchTouchEvent",
            {"type": type, "touchPoints": touch_points},
        )

    async def set_mobile_emulation(
        self,
        width: int,
        height: int,
        user_agent: str,
        device_scale: float = 2.0,
    ) -> None:
        """Enable mobile emulation via three CDP commands.

        Sends:
        - ``Emulation.setDeviceMetricsOverride`` (mobile=True, width, height,
          deviceScaleFactor)
        - ``Emulation.setUserAgentOverride``
        - ``Emulation.setTouchEmulationEnabled`` (enabled=True, maxTouchPoints=5)

        These are fire-and-forget from the caller's perspective (errors raise
        ``CDPError``); all three are awaited in order.
        """
        await self._send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": device_scale,
                "mobile": True,
            },
        )
        await self._send(
            "Emulation.setUserAgentOverride",
            {"userAgent": user_agent},
        )
        await self._send(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": True, "maxTouchPoints": 5},
        )

    async def screenshot(self) -> bytes:
        """Capture a PNG screenshot (``Page.captureScreenshot``).

        Returns raw PNG bytes.  Best-effort — raises ``CDPError`` on failure.
        """
        resp = await self._send("Page.captureScreenshot", {"format": "png"})
        data_b64 = resp.get("result", {}).get("data", "")
        return base64.b64decode(data_b64)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _discover_page_ws(self) -> str:
        """Fetch ``/json/list`` and return the first page target's WS URL.

        Uses ``asyncio.open_connection`` (stdlib only) to avoid pulling in an
        HTTP client just for this one-off probe.  Falls back to a constructed
        WS URL if the list is empty.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._port),
                timeout=self._connect_timeout,
            )
        except Exception as exc:
            raise CDPError(
                f"CDP HTTP unreachable at 127.0.0.1:{self._port}: {exc}"
            ) from exc

        try:
            request = (
                f"GET /json/list HTTP/1.0\r\n"
                f"Host: 127.0.0.1:{self._port}\r\n"
                f"\r\n"
            )
            writer.write(request.encode())
            await writer.drain()
            raw = await asyncio.wait_for(
                reader.read(65536), timeout=self._connect_timeout
            )
        finally:
            writer.close()

        body = _extract_http_body(raw)
        try:
            targets = json.loads(body)
        except json.JSONDecodeError:
            targets = []

        for t in targets:
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                return str(t["webSocketDebuggerUrl"])

        # No page target yet (browser just started); use the browser endpoint
        # which Chromium always exposes even before a tab is ready.
        logger.debug(
            "CDP /json/list had no page target on port %s; using /json/version endpoint",
            self._port,
        )
        return f"ws://127.0.0.1:{self._port}/json/version"

    async def _send(self, method: str, params: dict | None = None) -> dict:
        """Send a CDP JSON-RPC command and await the response.

        Returns the full CDP response dict.  Raises ``CDPError`` if:
        - the WebSocket is not connected
        - the response has an ``error`` field
        - the wait times out
        """
        if self._ws is None:
            raise CDPError("CDP not connected — call connect() first")

        cmd_id = self._next_id
        self._next_id += 1

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._pending[cmd_id] = fut

        payload = json.dumps({"id": cmd_id, "method": method, "params": params or {}})
        try:
            await self._ws.send(payload)
        except Exception as exc:
            self._pending.pop(cmd_id, None)
            fut.cancel()
            raise CDPError(f"CDP send failed ({method}): {exc}") from exc

        try:
            resp = await asyncio.wait_for(fut, timeout=self._cmd_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            raise CDPError(f"CDP command timed out ({method}, id={cmd_id})")

        if "error" in resp:
            err = resp["error"]
            raise CDPError(
                f"CDP error for {method}: [{err.get('code')}] {err.get('message')}"
            )
        return resp

    async def _recv_loop(self) -> None:
        """Background task: read CDP messages and resolve pending futures."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("CDP: malformed message (not JSON): %r", raw[:120])
                    continue
                cmd_id = msg.get("id")
                if cmd_id is not None and cmd_id in self._pending:
                    fut = self._pending.pop(cmd_id)
                    if not fut.done():
                        fut.set_result(msg)
                # Events (no id) are intentionally ignored at this layer;
                # Phase C (WS touch transport) will subscribe to them.
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("CDP recv loop exited: %s", exc)
            # Fail all pending futures
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(CDPError(f"CDP recv loop error: {exc}"))
            self._pending.clear()


def _extract_http_body(raw: bytes) -> str:
    """Extract the body from a raw HTTP/1.x response."""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    # Split on the blank line separating headers from body
    sep = "\r\n\r\n"
    idx = text.find(sep)
    if idx == -1:
        # Try Unix line endings
        sep = "\n\n"
        idx = text.find(sep)
    if idx == -1:
        return text
    return text[idx + len(sep):]
