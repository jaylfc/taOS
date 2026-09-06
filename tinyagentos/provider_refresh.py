"""Periodic cloud-provider catalog refresh.

Cloud providers (kilocode/openrouter/openai/…) gain and lose models upstream
over time. Without a refresh, taOS's LiteLLM ``model_list`` goes stale — a
newly published model 404s until someone PATCHes the provider or restarts.
This service re-probes the cloud providers on an interval and reloads LiteLLM
ONLY when the catalog actually changed (see
``providers.refresh_cloud_backends_if_changed``).

Mirrors AutoUpdateService's lifecycle (start/stop in the app lifespan).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# How often to re-probe cloud provider catalogs (seconds).
REFRESH_INTERVAL = 15 * 60
# No initial delay: fire the first probe immediately at startup so the
# models cache (seeded from config at boot) is replaced with a live
# LiteLLM result as soon as the proxy is up.  Earlier versions used a
# 120 s delay which left the picker empty for the first two minutes.
INITIAL_DELAY = 0


class CloudProviderRefresher:
    """Background task that keeps LiteLLM's model_list fresh with upstream."""

    def __init__(self, app_state, interval: float = REFRESH_INTERVAL,
                 initial_delay: float = INITIAL_DELAY):
        self._state = app_state
        self._interval = interval
        self._initial_delay = initial_delay
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="cloud-provider-refresh")
        logger.info("CloudProviderRefresher started (interval=%ds)", self._interval)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=self._initial_delay)
            return  # stopped during the initial delay
        except asyncio.TimeoutError:
            pass
        while True:
            try:
                await self._refresh_once()
            except Exception:
                logger.exception("cloud provider refresh failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                return
            except asyncio.TimeoutError:
                pass  # next cycle

    async def _refresh_once(self) -> None:
        # Imported lazily to avoid a route<-service import cycle at module load.
        from tinyagentos.routes.providers import (
            refresh_cloud_backends_if_changed,
            _fetch_litellm_models,
        )
        import time as _time

        config = getattr(self._state, "config", None)
        if config is None:
            return
        proxy = getattr(self._state, "llm_proxy", None)
        reloaded = await refresh_cloud_backends_if_changed(self._state, config, proxy)
        if reloaded:
            logger.info("cloud provider refresh: catalog changed, LiteLLM reloaded")
        # Always update the models cache after a background probe so the
        # picker serves a warm result without a live LiteLLM round-trip.
        # Skip when LiteLLM isn't running (nothing to read back yet).
        if proxy and proxy.is_running():
            data = await _fetch_litellm_models(proxy)
            if data:
                payload: dict = {"data": data, "object": "list"}
                self._state.litellm_models_cache = payload
                import asyncio as _asyncio
                self._state.litellm_models_cache_at = _asyncio.get_event_loop().time()
                self._state.litellm_models_cache_wallclock = _time.time()
                logger.debug(
                    "cloud provider refresh: models cache updated (%d models)", len(data)
                )
