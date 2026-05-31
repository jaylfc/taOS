"""Lazy backend proxy — start backends on first request, stop after idle TTL.

Wraps backend servers that load their model at process start and have no
built-in idle eviction (sd-server, llama-server, whisper.cpp server).

Pattern from docs/superpowers/specs/2026-04-11-taos-framework-integration-bridge-design.md
Phase 1.5 § Lazy lifecycle wrappers.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time

import httpx

logger = logging.getLogger(__name__)

_HEALTH_POLL_INTERVAL = 1.0
_COLD_START_TIMEOUT = 120.0
_STOP_GRACE_PERIOD = 10.0


class LazyBackendProxy:
    """Transparent TCP proxy with lazy subprocess lifecycle.

    Listens on ``proxy_port``.  On the first inbound connection, runs
    ``start_cmd`` to launch the real backend.  All subsequent connections are
    forwarded bidirectionally to ``backend_host:backend_port`` until the proxy
    has had *no active connections* for ``idle_timeout_seconds``, at which
    point the subprocess is stopped.  PWA / SSE / raw HTTP all pass through
    unchanged.
    """

    def __init__(
        self,
        *,
        proxy_port: int,
        backend_host: str = "127.0.0.1",
        backend_port: int,
        start_cmd: str,
        stop_cmd: str = "",
        idle_timeout_seconds: float = 300.0,
        health_url: str | None = None,
    ) -> None:
        if not start_cmd:
            raise ValueError("start_cmd is required")
        self._proxy_port = proxy_port
        self._backend_host = backend_host
        self._backend_port = backend_port
        self._start_cmd = start_cmd
        self._stop_cmd = stop_cmd
        self._idle_timeout = idle_timeout_seconds
        self._health_url = health_url or f"http://{backend_host}:{backend_port}/health"

        self._proc: subprocess.Popen | None = None
        self._last_request: float = 0.0
        self._active_connections: int = 0
        self._start_lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None
        self._server: asyncio.AbstractServer | None = None
        self._running = False

    # -- public API ----------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._proxy_port}"

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        """Start listening (does NOT start the subprocess yet)."""
        if self._running:
            return
        self._server = await asyncio.start_server(
            self._handle_connection,
            host="127.0.0.1",
            port=self._proxy_port,
        )
        self._running = True
        logger.info(
            "lazy-proxy :%d → %s:%d (idle=%.0fs)",
            self._proxy_port,
            self._backend_host,
            self._backend_port,
            self._idle_timeout,
        )

    async def stop(self) -> None:
        """Stop the proxy and the underlying subprocess."""
        if not self._running:
            return
        self._running = False
        self._cancel_idle()
        await self._stop_subprocess()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("lazy-proxy :%d stopped", self._proxy_port)

    # -- connection handler ---------------------------------------------------

    async def _handle_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        if not self._running:
            client_writer.close()
            return

        self._active_connections += 1
        self._cancel_idle()

        backend_writer: asyncio.StreamWriter | None = None

        try:
            try:
                await self._ensure_backend()
            except Exception:
                _write_503(client_writer)
                return

            try:
                backend_reader, backend_writer = await asyncio.wait_for(
                    asyncio.open_connection(self._backend_host, self._backend_port),
                    timeout=5.0,
                )
            except Exception:
                _write_503(client_writer)
                return

            # Bidirectional copy.
            async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
                try:
                    while True:
                        data = await src.read(65536)
                        if not data:
                            break
                        dst.write(data)
                        await dst.drain()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    pass

            try:
                await asyncio.gather(
                    _pipe(client_reader, backend_writer),
                    _pipe(backend_reader, client_writer),
                )
            except Exception:
                pass
        finally:
            if backend_writer is not None:
                backend_writer.close()
            client_writer.close()
            self._active_connections -= 1
            if self._running and self._active_connections == 0:
                await self._restart_idle_timer()

    # -- subprocess lifecycle ------------------------------------------------

    async def _ensure_backend(self) -> None:
        """Start the subprocess if it isn't already running."""
        if self._proc is not None and self._proc.poll() is None:
            return

        async with self._start_lock:
            if self._proc is not None and self._proc.poll() is None:
                return

            cold_start_started_at = time.monotonic()
            logger.info("lazy-proxy :%d → starting: %r", self._proxy_port, self._start_cmd)
            self._proc = subprocess.Popen(
                self._start_cmd,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            deadline = time.monotonic() + _COLD_START_TIMEOUT
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    raise RuntimeError(
                        f"Backend process exited with code {self._proc.returncode}"
                    )
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
                        resp = await client.get(self._health_url)
                        if 200 <= resp.status_code < 300:
                            logger.info(
                                "lazy-proxy :%d → healthy in %.2fs",
                                self._proxy_port,
                                time.monotonic() - cold_start_started_at,
                            )
                            return
                except Exception:
                    pass
                await asyncio.sleep(_HEALTH_POLL_INTERVAL)

            await self._kill_subprocess()
            elapsed = time.monotonic() - cold_start_started_at
            logger.warning(
                "lazy-proxy :%d → cold start timed out after %.2fs",
                self._proxy_port,
                elapsed,
            )
            raise TimeoutError(
                f"Backend at {self._backend_host}:{self._backend_port} "
                f"did not become healthy within {_COLD_START_TIMEOUT}s "
                f"(waited {elapsed:.1f}s)"
            )

    async def _stop_subprocess(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            return
        logger.info("lazy-proxy :%d → stopping backend", self._proxy_port)

        proc = self._proc  # keep local ref — _kill_subprocess clears self._proc

        if self._stop_cmd:
            stop_proc: subprocess.Popen | None = None
            try:
                stop_proc = subprocess.Popen(
                    self._stop_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                await asyncio.to_thread(stop_proc.wait, timeout=_STOP_GRACE_PERIOD)
            except subprocess.TimeoutExpired:
                if stop_proc:
                    stop_proc.kill()

        # Always terminate/wait the actual backend process.
        proc.terminate()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(proc.wait), timeout=_STOP_GRACE_PERIOD
            )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            proc.kill()
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(proc.wait), timeout=5
                )
            except (asyncio.TimeoutError, subprocess.TimeoutExpired):
                pass

        self._proc = None

    async def _kill_subprocess(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
            await asyncio.wait_for(
                asyncio.to_thread(self._proc.wait), timeout=5
            )
        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            pass
        self._proc = None

    # -- idle timer ----------------------------------------------------------

    async def _restart_idle_timer(self) -> None:
        self._last_request = time.monotonic()
        self._cancel_idle()
        if self._idle_timeout > 0:
            self._idle_task = asyncio.create_task(self._idle_expire())

    def _cancel_idle(self) -> None:
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    async def _idle_expire(self) -> None:
        elapsed = time.monotonic() - self._last_request
        remaining = self._idle_timeout - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        if self._running:
            logger.info(
                "lazy-proxy :%d → idle timeout (%.0fs), stopping backend",
                self._proxy_port,
                self._idle_timeout,
            )
            await self._stop_subprocess()


# -- helpers ------------------------------------------------------------------

def _write_503(writer: asyncio.StreamWriter) -> None:
    body = b'{"error":"backend unavailable"}'
    writer.write(
        b"HTTP/1.1 503 Service Unavailable\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"\r\n"
        + body
    )
    writer.close()
