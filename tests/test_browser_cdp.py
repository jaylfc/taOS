"""Tests for the CDP driver (browser_cdp.py) and the host-side CDP port wiring.

Uses a fake CDP server (asyncio websockets server speaking minimal CDP
JSON-RPC) to exercise BrowserCDP without a real Chromium instance.

Also covers the build_neko_run_args / PortAllocator changes that publish CDP
to 127.0.0.1 only.
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
import websockets
import websockets.asyncio.server as ws_server

from tinyagentos.worker.browser_cdp import BrowserCDP, CDPError
from tinyagentos.worker.browser_container import (
    DEFAULT_NEKO_CDP_IMAGE,
    DEFAULT_NEKO_GPU_IMAGE,
    DEFAULT_NEKO_IMAGE,
    BrowserContainerRunner,
    PortAllocator,
    build_neko_run_args,
)


# ---------------------------------------------------------------------------
# Fake CDP server helpers
# ---------------------------------------------------------------------------

class FakeCDPServer:
    """A minimal asyncio WebSocket server that speaks CDP JSON-RPC.

    Responds to any command with ``{"id": <id>, "result": {"ok": true}}``.
    Records every received message in ``self.received``.
    Also serves a minimal ``/json/list`` HTTP endpoint on the same TCP port
    (detected by reading the first bytes of the connection).
    """

    def __init__(self) -> None:
        self.received: list[dict] = []
        self._server: Any = None
        self._host = "127.0.0.1"
        self._port: int = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._port

    # ------------------------------------------------------------------
    # Server lifecycle (runs in a background thread with its own loop)
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        # Use port 0 to get an OS-assigned free port.
        async with ws_server.serve(
            self._handle_ws, self._host, 0
        ) as server:
            self._server = server
            # Retrieve the actual port from the socket.
            sockets = server.sockets
            if sockets:
                self._port = sockets[0].getsockname()[1]
            self._ready.set()
            await server.wait_closed()

    async def _handle_ws(self, websocket) -> None:
        async for message in websocket:
            try:
                cmd = json.loads(message)
            except json.JSONDecodeError:
                continue
            self.received.append(cmd)
            response = {"id": cmd.get("id"), "result": {"ok": True}}
            await websocket.send(json.dumps(response))


@pytest.fixture
def fake_cdp():
    """Start a FakeCDPServer and yield it; stop after the test."""
    srv = FakeCDPServer()
    srv.start()
    yield srv
    srv.stop()


# ---------------------------------------------------------------------------
# Helper: connect BrowserCDP to fake server bypassing /json/list discovery
# ---------------------------------------------------------------------------

class _DirectBrowserCDP(BrowserCDP):
    """BrowserCDP subclass that connects directly to the WS port (no /json/list)."""

    async def _discover_page_ws(self) -> str:
        return f"ws://127.0.0.1:{self._port}"


# ---------------------------------------------------------------------------
# Tests: BrowserCDP connect + attach
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_and_attach(fake_cdp):
    """BrowserCDP should connect to the fake CDP server without raising."""
    cdp = _DirectBrowserCDP(fake_cdp.port)
    await cdp.connect()
    assert cdp._ws is not None
    await cdp.close()


@pytest.mark.asyncio
async def test_context_manager(fake_cdp):
    """Async context manager should connect and close cleanly."""
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        assert cdp._ws is not None
    assert cdp._ws is None


# ---------------------------------------------------------------------------
# Tests: navigate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_navigate_sends_page_navigate(fake_cdp):
    """navigate() must send Page.navigate with the correct url parameter."""
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        await cdp.navigate("https://example.com")

    methods = [m["method"] for m in fake_cdp.received]
    assert "Page.navigate" in methods

    nav_cmd = next(m for m in fake_cdp.received if m["method"] == "Page.navigate")
    assert nav_cmd["params"]["url"] == "https://example.com"


# ---------------------------------------------------------------------------
# Tests: dispatch_touch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_touch_sends_correct_event(fake_cdp):
    """dispatch_touch must send Input.dispatchTouchEvent with the right shape."""
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        await cdp.dispatch_touch(
            [{"x": 100, "y": 200}, {"x": 150, "y": 250}],
            type="touchStart",
        )

    touch_cmd = next(
        m for m in fake_cdp.received if m["method"] == "Input.dispatchTouchEvent"
    )
    assert touch_cmd["params"]["type"] == "touchStart"
    points = touch_cmd["params"]["touchPoints"]
    assert len(points) == 2
    assert points[0]["x"] == 100.0
    assert points[0]["y"] == 200.0
    assert points[1]["x"] == 150.0
    assert points[1]["y"] == 250.0


@pytest.mark.asyncio
async def test_dispatch_touch_all_types(fake_cdp):
    """All four touch types should be accepted without raising."""
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        for touch_type in ("touchStart", "touchMove", "touchEnd", "touchCancel"):
            await cdp.dispatch_touch([{"x": 10, "y": 20}], type=touch_type)

    types_sent = [
        m["params"]["type"]
        for m in fake_cdp.received
        if m["method"] == "Input.dispatchTouchEvent"
    ]
    assert set(types_sent) == {"touchStart", "touchMove", "touchEnd", "touchCancel"}


@pytest.mark.asyncio
async def test_dispatch_touch_invalid_type_raises(fake_cdp):
    """An invalid touch type must raise ValueError before sending anything."""
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        with pytest.raises(ValueError, match="invalid type"):
            await cdp.dispatch_touch([{"x": 1, "y": 2}], type="pointerDown")


# ---------------------------------------------------------------------------
# Tests: set_mobile_emulation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_mobile_emulation_sends_three_commands(fake_cdp):
    """set_mobile_emulation must send the three expected CDP commands."""
    ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        await cdp.set_mobile_emulation(390, 844, ua)

    methods = [m["method"] for m in fake_cdp.received]
    assert "Emulation.setDeviceMetricsOverride" in methods
    assert "Emulation.setUserAgentOverride" in methods
    assert "Emulation.setTouchEmulationEnabled" in methods

    metrics_cmd = next(m for m in fake_cdp.received if m["method"] == "Emulation.setDeviceMetricsOverride")
    assert metrics_cmd["params"]["width"] == 390
    assert metrics_cmd["params"]["height"] == 844
    assert metrics_cmd["params"]["mobile"] is True
    assert metrics_cmd["params"]["deviceScaleFactor"] == 2.0

    ua_cmd = next(m for m in fake_cdp.received if m["method"] == "Emulation.setUserAgentOverride")
    assert ua_cmd["params"]["userAgent"] == ua

    touch_cmd = next(m for m in fake_cdp.received if m["method"] == "Emulation.setTouchEmulationEnabled")
    assert touch_cmd["params"]["enabled"] is True
    assert touch_cmd["params"]["maxTouchPoints"] == 5


@pytest.mark.asyncio
async def test_set_mobile_emulation_custom_scale(fake_cdp):
    """set_mobile_emulation should honour a custom device_scale parameter."""
    async with _DirectBrowserCDP(fake_cdp.port) as cdp:
        await cdp.set_mobile_emulation(375, 667, "TestUA", device_scale=3.0)

    metrics_cmd = next(m for m in fake_cdp.received if m["method"] == "Emulation.setDeviceMetricsOverride")
    assert metrics_cmd["params"]["deviceScaleFactor"] == 3.0


# ---------------------------------------------------------------------------
# Tests: graceful failure when CDP is unreachable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connect_raises_cdp_error_when_unreachable():
    """connect() must raise CDPError (not crash) when CDP is not listening."""
    cdp = BrowserCDP(19999, connect_timeout=0.2, cmd_timeout=1.0)
    with pytest.raises(CDPError):
        await cdp.connect()


@pytest.mark.asyncio
async def test_send_raises_when_not_connected():
    """Calling navigate() without connect() must raise CDPError immediately."""
    cdp = BrowserCDP(19999)
    with pytest.raises(CDPError, match="not connected"):
        await cdp.navigate("https://example.com")


# ---------------------------------------------------------------------------
# Tests: host-side CDP port wiring in build_neko_run_args
# ---------------------------------------------------------------------------

def _base_args(**overrides) -> dict:
    base = dict(
        container_name="c",
        profile_volume="v",
        node_ip="10.0.0.2",
        http_port=8800,
        epr_lo=59000,
        epr_hi=59009,
        user_pwd="u",
        admin_pwd="a",
    )
    base.update(overrides)
    return base


def test_cdp_host_port_published_to_loopback_only():
    """When cdp_host_port is given, the -p flag must use 127.0.0.1 (not 0.0.0.0)."""
    argv = build_neko_run_args(**_base_args(cdp_host_port=19200))
    # Find the CDP port publish flag
    cdp_flags = [
        argv[i + 1]
        for i, a in enumerate(argv)
        if a == "-p" and "9222" in argv[i + 1]
    ]
    assert len(cdp_flags) == 1, f"Expected exactly one CDP -p flag, got: {cdp_flags}"
    binding = cdp_flags[0]
    assert binding == "127.0.0.1:19200:9222", f"Wrong binding: {binding}"
    assert "0.0.0.0" not in binding


def test_cdp_host_port_not_in_args_when_none():
    """Stock/GPU images must not publish port 9222 at all."""
    argv = build_neko_run_args(**_base_args(cdp_host_port=None))
    assert "9222" not in " ".join(argv)


def test_cdp_args_without_cdp_host_port_at_all():
    """Omitting cdp_host_port entirely (default=None) also produces no CDP publish."""
    argv = build_neko_run_args(**_base_args())
    assert "9222" not in " ".join(argv)


# ---------------------------------------------------------------------------
# Tests: PortAllocator returns a 4-tuple with cdp_host_port
# ---------------------------------------------------------------------------

def test_port_allocator_returns_cdp_host_port():
    """allocate() must return (http_port, epr_lo, epr_hi, cdp_host_port) as a 4-tuple."""
    alloc = PortAllocator(http_base=8800, epr_base=59000, epr_span=10, cdp_base=19200)
    result = alloc.allocate()
    assert len(result) == 4
    http_port, epr_lo, epr_hi, cdp_host_port = result
    assert http_port == 8800
    assert cdp_host_port == 19200


def test_port_allocator_cdp_port_increments_with_slot():
    """Each allocation should use a distinct cdp_host_port."""
    alloc = PortAllocator(cdp_base=19200)
    _, _, _, cdp0 = alloc.allocate()
    _, _, _, cdp1 = alloc.allocate()
    assert cdp1 == cdp0 + 1


# ---------------------------------------------------------------------------
# Tests: BrowserContainerRunner wires cdp_host_port for CDP image
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _hw(*, soc="", gpu_type="none", cuda=False, vulkan=False):
    return SimpleNamespace(
        cpu=SimpleNamespace(soc=soc),
        gpu=SimpleNamespace(type=gpu_type, cuda=cuda, vulkan=vulkan),
    )


@pytest.mark.asyncio
async def test_runner_cdp_image_publishes_cdp_port():
    """RK3588 (CDP image) runner in mock mode must set cdp_url to host loopback."""
    hw = _hw(soc="rk3588")
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=hw)
    out = await runner.start(session_id="cdp-phase-b", profile_volume="v")
    assert out["cdp_url"] is not None
    # Must point to 127.0.0.1 (the host-local port), not the container-internal 9222
    assert out["cdp_url"].startswith("http://127.0.0.1:")
    assert out["cdp_host_port"] is not None
    # cdp_url port must match cdp_host_port
    import urllib.parse
    parsed = urllib.parse.urlparse(out["cdp_url"])
    assert parsed.port == out["cdp_host_port"]
    # Must NOT be the bare container port string if cdp_base != 9222
    # (i.e. the wiring uses the host port, not a hardcoded 9222)
    assert "0.0.0.0" not in out["cdp_url"]


@pytest.mark.asyncio
async def test_runner_stock_image_no_cdp():
    """Software-encode (stock) runner must return cdp_url=None and cdp_host_port=None."""
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=None)
    out = await runner.start(session_id="stock-session", profile_volume="v")
    assert out["cdp_url"] is None
    assert out["cdp_host_port"] is None


@pytest.mark.asyncio
async def test_runner_gpu_image_no_cdp():
    """NVIDIA GPU runner must not expose a CDP URL."""
    hw = _hw(gpu_type="nvidia", cuda=True)
    runner = BrowserContainerRunner(node_ip="10.0.0.2", mock=True, hw_profile=hw)
    out = await runner.start(session_id="gpu-session", profile_volume="v")
    assert out["cdp_url"] is None
    assert out["cdp_host_port"] is None
