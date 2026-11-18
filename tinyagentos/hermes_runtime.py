"""Hermes as the system taOS Agent's harness.

One OpenAI-compatible chat completion per turn, served by a hermes API server
at ``127.0.0.1:8642`` (configurable via ``taos_agent.hermes_url``). The server
must be reachable and the ``hermes_api_key`` secret must be present in the
device Secrets store. Mirrors :mod:`tinyagentos.claude_code_runtime`.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

HERMES_DEFAULT_URL = "http://127.0.0.1:8642"
HERMES_API_KEY_SECRET = "hermes_api_key"


def hermes_api_url(app_state: Any) -> str:
    """Return the hermes API base URL from config, or the default.

    Reads ``taos_agent.hermes_url`` from app_state.config, falls back to
    :data:`HERMES_DEFAULT_URL`, and strips any trailing slash.
    """
    config = getattr(app_state, "config", None)
    taos_agent = getattr(config, "taos_agent", None) if config is not None else None
    if isinstance(taos_agent, dict):
        url = taos_agent.get("hermes_url")
        if isinstance(url, str) and url.strip():
            return url.strip().rstrip("/")
    return HERMES_DEFAULT_URL


async def hermes_key_present(app_state: Any) -> bool:
    """Whether the Secrets store holds the hermes_api_key secret.

    Presence only: the value is read and dropped, never logged or returned.
    """
    store = getattr(app_state, "secrets", None)
    if store is None:
        return False
    try:
        row = await store.get(HERMES_API_KEY_SECRET)
    except Exception:  # noqa: BLE001 - a broken store means no key
        logger.warning("hermes_runtime: could not read the secrets store")
        return False
    return bool(row and row.get("value"))


async def hermes_api_up(app_state: Any) -> bool:
    """Check if the hermes API server is reachable.

    Does ``GET {url}/health`` with a 2 second timeout. Returns ``True`` only
    on HTTP 200; any exception (timeout, connection error, non-200 status)
    returns ``False``.
    """
    url = hermes_api_url(app_state)
    health_url = f"{url}/health"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(health_url)
        return resp.status_code == 200
    except Exception:  # noqa: BLE001 - any error means the API is not up
        return False