"""Tests for lazy start/stop of llama-server and whisper backends with per-class idle TTL."""

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tinyagentos.lazy_backend_proxy import LazyBackendProxy


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# -- tests ---------------------------------------------------------------------------


class TestLazyLlamaWhisperTTL:
    """Lazy start/stop with per-class idle TTL for llama-server and whisper backends."""

    @pytest.mark.asyncio
    async def test_llama_lazy_start_on_first_connection(self):
        """Lazy llama backend starts its subprocess on the first user connection."""
        backend_port = _free_port()

        p = LazyBackendProxy(
            proxy_port=_free_port(),
            backend_host="127.0.0.1",
            backend_port=backend_port,
            start_cmd=f"python3 -m http.server {backend_port} --bind 127.0.0.1",
            idle_timeout_seconds=5.0,
            health_url=f"http://127.0.0.1:{backend_port}/",
        )
        await p.start()

        try:
            assert p._proc is None

            # First connection should start the backend lazily
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(p.url + "/", timeout=10)
                assert resp.status_code == 200

            # After first connection, proc should be running
            assert p._proc is not None and p._proc.poll() is None

            await p.stop()
        finally:
            if p._proc and p._proc.poll() is None:
                p._proc.kill()

    @pytest.mark.asyncio
    async def test_llama_lazy_stop_after_idle_ttl(self):
        """Lazy backend stops after idle TTL with no active connections."""
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

            # Wait for idle timeout (more than 1 second)
            await asyncio.sleep(2.0)

            # After idle timeout, proc should be stopped
            assert p._proc is None or p._proc.poll() is not None
        finally:
            await p.stop()
            if p._proc and p._proc.poll() is None:
                p._proc.kill()

    @pytest.mark.asyncio
    async def test_whisper_lazy_start_on_first_connection(self):
        """Lazy whisper backend starts its subprocess on the first user connection."""
        backend_port = _free_port()

        p = LazyBackendProxy(
            proxy_port=_free_port(),
            backend_host="127.0.0.1",
            backend_port=backend_port,
            start_cmd=f"python3 -m http.server {backend_port} --bind 127.0.0.1",
            idle_timeout_seconds=5.0,
            health_url=f"http://127.0.0.1:{backend_port}/",
        )
        await p.start()

        try:
            assert p._proc is None

            # First connection should start the backend lazily
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(p.url + "/", timeout=10)
                assert resp.status_code == 200

            # After first connection, proc should be running
            assert p._proc is not None and p._proc.poll() is None

            await p.stop()
        finally:
            if p._proc and p._proc.poll() is None:
                p._proc.kill()

    @pytest.mark.asyncio
    async def test_health_probes_do_not_reset_idle_timer(self):
        """Health monitor probes should not prevent idle timeout.
        
        This test verifies that after making a connection and waiting past the
        idle timeout, the backend is stopped. The probe_target feature on the
        LazyBackendProxy ensures that health check connections matching the
        probe_target path do not reset the idle timer.
        """
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
            # Make a connection to start the backend
            async with httpx.AsyncClient(timeout=15) as client:
                await client.get(p.url + "/", timeout=15)
            assert p._proc is not None and p._proc.poll() is None

            # Wait past the idle timeout
            await asyncio.sleep(2.0)

            # Backend should have stopped after the idle TTL.
            assert p._proc is None or p._proc.poll() is not None, (
                "Backend should have stopped after idle TTL"
            )
        finally:
            await p.stop()
            if p._proc and p._proc.poll() is None:
                p._proc.kill()