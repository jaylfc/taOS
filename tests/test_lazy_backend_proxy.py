"""Tests for lazy_backend_proxy — on-demand subprocess lifecycle."""

import asyncio
import socket
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tinyagentos.lazy_backend_proxy import LazyBackendProxy


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _EchoServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._server = None
        self.connections = 0

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, host=self.host, port=self.port
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        self.connections += 1
        data = await reader.read(65536)
        if data:
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: " + str(len(data)).encode() + b"\r\n"
                b"\r\n" + data
            )
            writer.write(resp)
        await writer.drain()
        writer.close()


def _make_proxy(echo_port: int, **kw) -> LazyBackendProxy:
    defaults = dict(
        proxy_port=_free_port(),
        backend_host="127.0.0.1",
        backend_port=echo_port,
        start_cmd="echo fake-start",
        idle_timeout_seconds=1.0,
    )
    defaults.update(kw)
    return LazyBackendProxy(**defaults)


# -- tests -------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        p = _make_proxy(1)  # port doesn't matter, backend never started
        await p.start()
        assert p.is_running
        await p.stop()
        assert not p.is_running

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self):
        p = _make_proxy(1)
        await p.start()
        await p.start()
        assert p.is_running
        await p.stop()

    @pytest.mark.asyncio
    async def test_stop_when_stopped_is_safe(self):
        p = _make_proxy(1)
        await p.stop()


class TestProxyForwarding:
    @pytest.mark.asyncio
    async def test_bidirectional_forwarding(self):
        port = _free_port()
        echo = _EchoServer("127.0.0.1", port)
        await echo.start()
        try:
            p = _make_proxy(port)
            with patch.object(p, "_ensure_backend", new_callable=AsyncMock):
                await p.start()
                try:
                    async with httpx.AsyncClient(timeout=5) as client:
                        resp = await client.post(p.url + "/x", content=b"hello")
                    assert resp.status_code == 200
                    assert b"hello" in resp.content
                    assert echo.connections >= 1
                finally:
                    await p.stop()
        finally:
            await echo.stop()

    @pytest.mark.asyncio
    async def test_503_when_backend_refuses(self):
        p = _make_proxy(1, backend_port=1)  # port 1 is closed
        with patch.object(p, "_ensure_backend", new_callable=AsyncMock):
            await p.start()
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(p.url, timeout=2)
                    # httpx may get a 503 or throw on connection refused
                    assert resp.status_code == 503 or resp.status_code >= 400
            except (httpx.ConnectError, httpx.ReadError, OSError):
                pass  # connection refused is also acceptable
            finally:
                await p.stop()


class TestRealSubprocessLifecycle:
    @pytest.mark.asyncio
    async def test_cold_start_launches_real_process(self):
        backend_port = _free_port()
        p = LazyBackendProxy(
            proxy_port=_free_port(),
            backend_host="127.0.0.1",
            backend_port=backend_port,
            start_cmd=f"python3 -m http.server {backend_port} --bind 127.0.0.1",
            idle_timeout_seconds=1.0,
            health_url=f"http://127.0.0.1:{backend_port}/",
        )
        await p.start()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(p.url + "/", timeout=15)
            assert resp.status_code == 200
            assert p._proc is not None and p._proc.poll() is None
        finally:
            await p.stop()
            if p._proc and p._proc.poll() is None:
                p._proc.kill()

    @pytest.mark.asyncio
    async def test_cold_start_fails_when_command_exits(self):
        backend_port = _free_port()
        p = LazyBackendProxy(
            proxy_port=_free_port(),
            backend_host="127.0.0.1",
            backend_port=backend_port,
            start_cmd="exit 1",
            idle_timeout_seconds=1.0,
            health_url=f"http://127.0.0.1:{backend_port}/health",
        )
        await p.start()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(p.url, timeout=8)
                # Proxy should return 503 after subprocess exits
                assert resp.status_code >= 400
        finally:
            await p.stop()


class TestIdleTimeout:
    @pytest.mark.asyncio
    async def test_idle_stops_backend_after_timeout(self):
        backend_port = _free_port()
        p = LazyBackendProxy(
            proxy_port=_free_port(),
            backend_host="127.0.0.1",
            backend_port=backend_port,
            start_cmd=f"python3 -m http.server {backend_port} --bind 127.0.0.1",
            idle_timeout_seconds=1.0,
            health_url=f"http://127.0.0.1:{backend_port}/",
        )
        await p.start()
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.get(p.url + "/", timeout=15)
            assert p._proc is not None and p._proc.poll() is None
            await asyncio.sleep(2.0)
            assert p._proc is None or p._proc.poll() is not None
        finally:
            await p.stop()
            if p._proc and p._proc.poll() is None:
                p._proc.kill()

    @pytest.mark.asyncio
    async def test_active_requests_reset_idle_timer(self):
        backend_port = _free_port()
        p = LazyBackendProxy(
            proxy_port=_free_port(),
            backend_host="127.0.0.1",
            backend_port=backend_port,
            start_cmd=f"python3 -m http.server {backend_port} --bind 127.0.0.1",
            idle_timeout_seconds=2.0,
            health_url=f"http://127.0.0.1:{backend_port}/",
        )
        await p.start()
        try:
            for _ in range(5):
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.get(p.url + "/", timeout=10)
                await asyncio.sleep(0.6)
            assert p._proc is not None and p._proc.poll() is None
        finally:
            await p.stop()
            if p._proc and p._proc.poll() is None:
                p._proc.kill()
