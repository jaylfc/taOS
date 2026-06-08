"""Provider lifecycle management.

Starts and stops backend services on demand, manages keep-alive timers,
and exposes graceful drain + kill-now stop paths.

keep_alive_minutes semantics:
  0   = always on — timer is never started, service is never auto-stopped
  >0  = stop after N minutes of idle (no in-flight tasks)
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyagentos.scheduler.backend_catalog import BackendCatalog

logger = logging.getLogger(__name__)

_DRAIN_TIMEOUT_SECONDS = 60


class LifecycleManager:
    """Manages start/stop lifecycle for auto-managed backend services."""

    def __init__(self, catalog: "BackendCatalog") -> None:
        self._catalog = catalog
        self._keepalive_tasks: dict[str, asyncio.Task] = {}
        # Optional shared httpx.AsyncClient injected from app.state.http_client.
        # When set, _probe_health reuses it instead of opening a new connection
        # per poll (avoids TCP churn during startup health polling).
        self.shared_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, name: str) -> None:
        """Start a stopped backend service.

        Runs start_cmd, polls /health until the service responds, then sets
        lifecycle_state to "running". Raises TimeoutError if the service
        does not respond within startup_timeout_seconds.
        """
        backend = self._backend_config(name)
        start_cmd = backend.get("start_cmd", "")
        timeout = backend.get("startup_timeout_seconds", 60)

        self._catalog.set_lifecycle_state(name, "starting")
        logger.info("lifecycle: starting %s via %r", name, start_cmd)

        # Set deadline before launching the command so startup-script time
        # is counted against the same budget as the health-poll window.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        if start_cmd:
            proc = await asyncio.create_subprocess_shell(
                start_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                self._catalog.set_lifecycle_state(name, "stopped")
                raise TimeoutError(
                    f"Service {name!r} start_cmd did not complete within {timeout}s"
                )

        # Poll health until ready or deadline
        url = backend.get("url", "")
        while loop.time() < deadline:
            if await self._probe_health(url):
                self._catalog.set_lifecycle_state(name, "running")
                logger.info("lifecycle: %s is running", name)
                return
            await asyncio.sleep(2)

        self._catalog.set_lifecycle_state(name, "stopped")
        raise TimeoutError(
            f"Service {name!r} did not respond within {timeout}s"
        )

    async def drain_and_stop(self, name: str, force: bool = False) -> None:
        """Stop a running backend service.

        If force=False: waits up to _DRAIN_TIMEOUT_SECONDS for in-flight
        tasks to finish (graceful drain), then runs stop_cmd.
        If force=True: runs stop_cmd immediately (kill now).
        """
        backend = self._backend_config(name)
        stop_cmd = backend.get("stop_cmd", "")

        self._catalog.set_lifecycle_state(name, "draining")
        self._cancel_keepalive(name)

        if not force:
            try:
                await asyncio.wait_for(
                    self._wait_for_drain(name),
                    timeout=_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("lifecycle: drain timeout for %s, forcing stop", name)

        self._catalog.set_lifecycle_state(name, "stopping")
        logger.info("lifecycle: stopping %s via %r", name, stop_cmd)

        if stop_cmd:
            proc = await asyncio.create_subprocess_shell(
                stop_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=_DRAIN_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("lifecycle: stop_cmd timed out for %s, killed", name)

        self._catalog.set_lifecycle_state(name, "stopped")
        logger.info("lifecycle: %s stopped", name)

    def notify_task_complete(self, name: str) -> None:
        """Call this when a task on a backend completes.

        Starts or resets the keep-alive timer for the backend.
        If keep_alive_minutes == 0, no timer is started (always-on).
        Callers must check == 0, not truthiness, because 0 is intentional.
        """
        backend = self._backend_config(name)
        keep_alive = backend.get("keep_alive_minutes", 10)
        if keep_alive == 0:
            return
        self._cancel_keepalive(name)
        delay = keep_alive * 60
        task = asyncio.create_task(
            self._keepalive_expire(name, delay),
            name=f"keepalive-{name}",
        )
        self._keepalive_tasks[name] = task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _probe_health(self, url: str) -> bool:
        """Return True if the service at url responds to /health with status ok.

        Uses ``self.shared_client`` when available to avoid per-probe TCP
        connection churn.  Falls back to a one-shot client if no shared client
        has been injected (e.g. during tests or early startup).
        """
        import httpx
        probe_url = f"{url.rstrip('/')}/health"
        try:
            if self.shared_client is not None:
                resp = await self.shared_client.get(probe_url, timeout=3)
            else:
                async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                    resp = await client.get(probe_url, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("status") in ("ok", "healthy", "running")
        except Exception:
            pass
        return False

    async def _wait_for_drain(self, name: str) -> None:
        """Wait until the catalog reports no in-flight tasks for this backend."""
        while True:
            count = getattr(self._catalog, "in_flight_count", lambda n: 0)(name)
            if count == 0:
                return
            await asyncio.sleep(1)

    async def _keepalive_expire(self, name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._catalog.get_lifecycle_state(name) != "running":
            return
        logger.info("lifecycle: keep-alive expired for %s, stopping", name)
        try:
            await self.drain_and_stop(name, force=False)
        except Exception:
            logger.exception("lifecycle: stop failed for %s after keep-alive", name)

    def _cancel_keepalive(self, name: str) -> None:
        task = self._keepalive_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

    def _backend_config(self, name: str) -> dict:
        for b in self._catalog._backends_config:
            if b.get("name") == name:
                return b
        return {}
