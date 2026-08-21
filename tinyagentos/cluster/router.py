from __future__ import annotations
import logging
import httpx
from tinyagentos.cluster.manager import ClusterManager

logger = logging.getLogger(__name__)


class TaskRouter:
    """Routes inference requests to the best available worker.

    taOS #640 Fix 3: Circuit breaker integration.  Workers that fail
    repeatedly within a time window are skipped so a single slow/failing
    worker no longer blocks routing for ``timeout * N_workers`` seconds.
    """

    def __init__(self, cluster: ClusterManager, http_client: httpx.AsyncClient):
        self.cluster = cluster
        self.http_client = http_client

    async def route_request(self, capability: str, method: str, path: str,
                            body: dict | None = None, timeout: float = 60) -> tuple[dict | None, str | None]:
        """Route a request to the best worker for the given capability.

        Returns (response_data, worker_name) or (None, None) if all fail.
        Workers with an open circuit breaker (Fix 3) are skipped.
        """
        workers = self.cluster.get_workers_for_capability(capability)
        ft = self.cluster.failure_tracker  # may be None (tests without tracker)

        for worker in workers:
            # taOS #640 Fix 3: skip workers whose circuit breaker is tripped.
            if ft is not None and ft.is_tripped(worker.name):
                logger.debug(
                    "Skipping worker '%s' — circuit breaker tripped", worker.name
                )
                continue

            try:
                url = f"{worker.url.rstrip('/')}{path}"
                if method == "GET":
                    resp = await self.http_client.get(url, timeout=timeout)
                else:
                    resp = await self.http_client.post(url, json=body, timeout=timeout)
                resp.raise_for_status()
                # Success — clear any failure record for this worker.
                if ft is not None:
                    ft.record_success(worker.name)
                return resp.json(), worker.name
            except httpx.HTTPStatusError as e:
                # 4xx = client error (bad request, unauthorized, etc.) —
                # do NOT trip the circuit breaker; healthy workers should
                # not be penalised for malformed or unauthorised requests.
                # 5xx = server error — DO record as a failure.
                status = getattr(e.response, "status_code", 0) if e.response else 0
                try:
                    status = int(status)
                except (TypeError, ValueError):
                    status = 0
                if status >= 500:
                    logger.warning(
                        "Worker '%s' failed for %s: 5xx %d",
                        worker.name, capability, e.response.status_code,
                    )
                    if ft is not None:
                        ft.record_failure(worker.name)
                else:
                    logger.debug(
                        "Worker '%s' returned client error %d for %s (not recorded)",
                        worker.name, e.response.status_code, capability,
                    )
                continue
            except Exception as e:
                # Transport errors, timeouts, etc. — record as failure.
                logger.warning(f"Worker '{worker.name}' failed for {capability}: {e}")
                if ft is not None:
                    ft.record_failure(worker.name)
                continue

        return None, None

    async def embed(self, text: str) -> tuple[dict | None, str | None]:
        return await self.route_request("embed", "POST", "/embed", {"text": text})

    async def chat(self, messages: list[dict], model: str | None = None) -> tuple[dict | None, str | None]:
        body = {"messages": messages}
        if model:
            body["model"] = model
        return await self.route_request("chat", "POST", "/v1/chat/completions", body)

    async def generate_image(self, prompt: str, **kwargs) -> tuple[dict | None, str | None]:
        body = {"prompt": prompt, **kwargs}
        return await self.route_request("image-generation", "POST", "/v1/images/generations", body, timeout=120)
