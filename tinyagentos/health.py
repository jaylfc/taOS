from __future__ import annotations

import asyncio
import logging
import time

import httpx
import psutil

from tinyagentos.backend_adapters import check_backend_health
from tinyagentos.config import AppConfig
from tinyagentos.metrics import MetricsStore
from tinyagentos.notifications import NotificationStore
from tinyagentos.qmd_client import QmdClient
from tinyagentos.agent_db import get_agent_db

logger = logging.getLogger(__name__)


class HealthMonitor:
    def __init__(
        self,
        config: AppConfig,
        metrics: MetricsStore,
        qmd_client: QmdClient,
        http_client: httpx.AsyncClient,
        notifications: NotificationStore | None = None,
    ):
        self.config = config
        self.metrics = metrics
        self.qmd_client = qmd_client
        self.http_client = http_client
        self.notifications = notifications
        self._task: asyncio.Task | None = None
        self._poll_count = 0
        self._last_cleanup = 0
        self._backend_states: dict[str, str] = {}
        self._poll_budget_seconds: float | None = None  # set in tests

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        interval = self.config.metrics.get("poll_interval", 30)
        # Hard ceiling so a wedged backend or hung qmd probe can't lock the
        # event loop. Each individual call already has its own timeout, but
        # this is a defense-in-depth guard against #323-style wedges.
        poll_budget = self._poll_budget_seconds or max(60, interval * 2)
        while True:
            try:
                await asyncio.wait_for(self._poll_once(), timeout=poll_budget)
            except asyncio.TimeoutError:
                logger.error(f"Health poll exceeded {poll_budget}s budget — aborting cycle")
            except Exception as e:
                logger.error(f"Health poll error: {e}")
            await asyncio.sleep(interval)

    async def _poll_once(self) -> None:
        now = int(time.time())
        self._poll_count += 1

        # 1. Backend health
        for backend in self.config.backends:
            try:
                result = await check_backend_health(self.http_client, backend)
                status_val = 1.0 if result["status"] == "ok" else 0.0
                await self.metrics.insert(f"backend.{backend['name']}.status", status_val, now)
                await self.metrics.insert(
                    f"backend.{backend['name']}.response_ms",
                    float(result.get("response_ms", 0)),
                    now,
                )
                # Notify on state changes only
                current = result["status"]
                prev = self._backend_states.get(backend["name"])
                if prev is not None and prev != current and self.notifications:
                    if current != "ok":
                        await self.notifications.emit_event(
                            "backend.down",
                            f"Backend '{backend['name']}' is unreachable",
                            f"Health check failed for {backend['url']}",
                            level="warning",
                        )
                    else:
                        await self.notifications.emit_event(
                            "backend.up",
                            f"Backend '{backend['name']}' recovered",
                            f"Health check succeeded for {backend['url']}",
                            level="info",
                        )
                self._backend_states[backend["name"]] = current
            except Exception as e:
                logger.warning(f"Backend health check failed for {backend['name']}: {e}")

        # 2. System resources
        await self.metrics.insert("system.cpu_pct", psutil.cpu_percent(), now)
        mem = psutil.virtual_memory()
        await self.metrics.insert("system.ram_pct", mem.percent, now)
        disk = psutil.disk_usage("/")
        await self.metrics.insert("system.disk_pct", disk.percent, now)

        # 3. QMD health
        qmd_health = await self.qmd_client.health()
        qmd_status = 1.0 if qmd_health.get("status") != "error" else 0.0
        await self.metrics.insert("qmd.status", qmd_status, now)
        await self.metrics.insert("qmd.health_response_ms", float(qmd_health.get("response_ms", 0)), now)

        # 4. Agent vector counts (every 10th cycle)
        if self._poll_count % 10 == 0:
            for agent in self.config.agents:
                db = get_agent_db(agent)
                if db:
                    count = db.vector_count()
                    await self.metrics.insert(
                        f"agent.{agent['name']}.vectors", float(count), now,
                        labels={"agent": agent["name"]},
                    )

        # 5. Daily retention cleanup
        if now - self._last_cleanup > 86400:
            retention_days = self.config.metrics.get("retention_days", 30)
            deleted = await self.metrics.cleanup(retention_days)
            if deleted:
                logger.info(f"Metrics cleanup: deleted {deleted} old rows")
            self._last_cleanup = now
