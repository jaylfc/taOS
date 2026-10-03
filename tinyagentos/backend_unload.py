"""Per-backend model unload adapters: the residency backend of the GPU work
queue (#3230; docs/design/plans/2026-07-17-gpu-work-queue-plan.md, slice B1).

``unload_model`` is the queue's eviction mechanism. It is called only from the
arbiter's admission/eviction path (spec section 3.3): there is no HTTP route
or other hook for it, and nothing else should import this module.

Registered backends:

- ``llama-swap`` (#3229): ``POST {base}/api/models/unload/{model}`` stops that
  model's upstream process (llama-swap README, "/api/models/unload/:model_id";
  internal/server/apigroup.go handleAPIUnloadModel, verified against v261).
  It resolves aliases, answers 200 when the model was not running, and 404 for
  a model it does not know. llama-swap's unload-all (``POST
  /api/models/unload``, ``GET /unload``) is never used: it would evict models
  the queue considers active.

Slice B1 adds ollama and rkllama to ``_UNLOADERS``; llama-cpp and vllm are
single-model servers and stay not unload-capable.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


async def _unload_llama_swap(client: httpx.AsyncClient, base_url: str, model: str,
                             timeout: float) -> httpx.Response:
    return await client.post(
        f"{base_url}/api/models/unload/{quote(model, safe='')}", timeout=timeout,
    )


_UNLOADERS: dict[str, Callable[[httpx.AsyncClient, str, str, float],
                               Awaitable[httpx.Response]]] = {
    "llama-swap": _unload_llama_swap,
}

UNLOAD_CAPABLE_TYPES: frozenset[str] = frozenset(_UNLOADERS)


def unload_capable(backend_type: str) -> bool:
    return backend_type in UNLOAD_CAPABLE_TYPES


async def unload_model(client: httpx.AsyncClient, *, backend_type: str,
                       base_url: str, model: str, timeout: float = 15.0) -> bool:
    """Ask the backend to unload *model* now. Returns True on 2xx.

    Never raises: an unknown type returns False without a request, and
    connection errors and non-2xx log and return False. Whether VRAM was
    actually freed is for the caller to confirm by re-polling residency.
    """
    unloader = _UNLOADERS.get(backend_type)
    if unloader is None:
        return False
    if model in ("", ".", ".."):
        # Dot segments are removed from the URL path: "." would reach
        # llama-swap's unload-ALL route (POST /api/models/unload).
        logger.debug("refusing to unload invalid model name %r on %s", model, base_url)
        return False
    try:
        resp = await unloader(client, base_url.rstrip("/"), model, timeout)
    except Exception as exc:  # noqa: BLE001 - eviction must never raise
        logger.warning("unload of %s on %s %s failed: %s", model, backend_type, base_url, exc)
        return False
    if not resp.is_success:
        logger.warning("unload of %s on %s %s returned HTTP %s",
                       model, backend_type, base_url, resp.status_code)
        return False
    return True
