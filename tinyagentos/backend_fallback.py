from __future__ import annotations

import logging
import time

import httpx

logger = logging.getLogger(__name__)


class BackendFallback:
    """Try backends in priority order, fall back on failure."""

    def __init__(self, backends: list[dict], http_client: httpx.AsyncClient):
        self.backends = sorted(backends, key=lambda b: b.get("priority", 99))
        self.http_client = http_client
        self._last_healthy: dict[str, float] = {}  # name -> last successful time
        self._last_failed: dict[str, float] = {}   # name -> last failure time
        self._backoff_seconds = 30

    async def request(self, method: str, path: str, **kwargs) -> tuple[dict | None, str | None]:
        """Make a request to the highest-priority healthy backend.

        Returns (response_data, backend_name) or (None, None) if all fail.
        """
        timeout = kwargs.pop("timeout", 30)

        for backend in self.backends:
            name = backend["name"]
            url = backend["url"].rstrip("/")

            # Skip backends that failed recently (back off for 30s)
            last_fail = self._last_failed.get(name, 0)
            if time.time() - last_fail < self._backoff_seconds and name in self._last_failed:
                logger.debug(f"Skipping backend '{name}' (backoff)")
                continue

            try:
                if method.upper() == "GET":
                    resp = await self.http_client.get(
                        f"{url}{path}", timeout=timeout,
                    )
                else:
                    resp = await self.http_client.post(
                        f"{url}{path}", timeout=timeout, **kwargs,
                    )
                resp.raise_for_status()
                self._last_healthy[name] = time.time()
                self._last_failed.pop(name, None)
                return resp.json(), name
            except Exception as e:
                logger.warning(f"Backend '{name}' ({url}) failed: {e}")
                self._last_failed[name] = time.time()
                continue

        return None, None

    async def get_healthy_backend(self) -> dict | None:
        """Return the highest-priority healthy backend."""
        for backend in self.backends:
            url = backend["url"].rstrip("/")
            try:
                resp = await self.http_client.get(f"{url}/health", timeout=5)
                if resp.status_code == 200:
                    return backend
            except Exception:
                continue
        return None

    def get_status(self) -> list[dict]:
        """Return status of all backends."""
        now = time.time()
        result = []
        for b in self.backends:
            name = b["name"]
            last_healthy = self._last_healthy.get(name)
            last_failed = self._last_failed.get(name)

            if last_healthy and not last_failed:
                status = "healthy"
            elif last_failed and not last_healthy:
                status = "down"
            elif last_failed and last_healthy:
                status = "down" if last_failed > last_healthy else "healthy"
            else:
                status = "unknown"

            result.append({
                "name": name,
                "url": b["url"],
                "priority": b.get("priority", 99),
                "last_healthy": last_healthy,
                "last_failed": last_failed,
                "status": status,
            })
        return result

    def get_primary_backend(self) -> dict | None:
        """Return the highest-priority backend that is not currently marked as failed."""
        for b in self.backends:
            name = b["name"]
            last_fail = self._last_failed.get(name, 0)
            if time.time() - last_fail < self._backoff_seconds and name in self._last_failed:
                continue
            return b
        return None
