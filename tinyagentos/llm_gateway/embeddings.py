"""``POST /embeddings``: the gateway's embeddings, to the backends LiteLLM used.

Routing comes from the same table as chat (``litellm_config.build_model_list``),
but WITH the ollama ``/api/tags`` probe: embedding entries, including the
``taos-embedding-default`` alias (``EMBEDDING_ALIAS``) the deployer injects as
``TAOS_EMBEDDING_MODEL``, exist only from that probe. The probe result is
cached on the app state for ``DISCOVERY_TTL_SECONDS`` so an agent embedding in
a loop does not pay a 2s probe per ollama backend per call.

Per backend, the call LiteLLM made:

- ``ollama`` / ``ollama_chat`` (ollama, rkllama, hailo-ollama):
  ``POST <api_base>/api/embed`` with ``{"model", "input": [...]}``; the answer's
  ``embeddings`` + ``prompt_eval_count`` become an OpenAI ``list`` response;
- ``openai`` / ``openrouter`` (llama.cpp, vLLM, any OpenAI-compatible server):
  ``POST <base>/embeddings`` with the caller's body, ``model`` rewritten;
- anything else: 501 ``backend_not_supported``.

Failover, cooldowns, key handling and error redaction are the chat path's
(``forward``). Usage is recorded to the caller's trace exactly as for chat
(``kind=llm_call``, output tokens 0).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from tinyagentos import litellm_config
from tinyagentos.llm_gateway import forward as _forward
from tinyagentos.llm_gateway.errors import GatewayError, bad_request, upstream_error
from tinyagentos.llm_gateway.resolve import Route, route_from_entry
from tinyagentos.llm_usage.pricing import Cost, cost_of
from tinyagentos.llm_usage.usage import UNKNOWN, Usage

logger = logging.getLogger(__name__)

DISCOVERY_TTL_SECONDS = 60.0
# Seconds to wait for each ollama /api/tags probe (LiteLLM's config writer uses 2s too).
_PROBE_TIMEOUT = 2.0
_STATE_CACHE = "llm_gateway_embedding_discovery"


def _backends(state) -> list[dict]:
    config = getattr(state, "config", None)
    return list(getattr(config, "backends", None) or [])


async def _discovered(state) -> dict[str, list[str]]:
    """``{ollama url: [model names]}``, probed at most once per TTL."""
    backends = _backends(state)
    key = json.dumps(sorted(
        (b.get("url") or "").rstrip("/") for b in backends
        if b.get("type", "ollama") in ("ollama", "rkllama", "hailo-ollama") and b.get("url")
    ))
    cached = getattr(state, _STATE_CACHE, None)
    now = time.monotonic()
    if isinstance(cached, tuple) and cached[0] == key and now - cached[1] < DISCOVERY_TTL_SECONDS:
        return cached[2]
    found = await litellm_config._discover_ollama_backends_concurrent(backends, timeout=_PROBE_TIMEOUT)
    try:
        setattr(state, _STATE_CACHE, (key, now, found))
    except Exception:  # noqa: BLE001 - a frozen state just means no cache
        pass
    return found


async def embedding_table(state) -> list[dict]:
    """The routing table with the probe-derived embedding entries included."""
    return litellm_config.build_model_list(
        _backends(state),
        registry=getattr(state, "registry", None),
        discovered=await _discovered(state),
        discover=False,
    )


def _route(entry: dict, name: str) -> Route:
    return route_from_entry(entry, name)


def find_embedding_routes(table: list[dict], name: str) -> list[Route]:
    """Every entry for ``name``, embedding-mode ones first, each group in
    table (priority) order. A model on a llama.cpp backend is listed as a
    plain (chat-mode) entry, and LiteLLM embedded through it all the same."""
    embed, other = [], []
    for entry in table:
        if entry.get("model_name") != name:
            continue
        mode = (entry.get("model_info") or {}).get("mode")
        (embed if mode == "embedding" else other).append(_route(entry, name))
    return embed + other


def servable(route: Route) -> bool:
    return (route.provider in _forward.OLLAMA_PROVIDERS
            or route.provider in _forward.OPENAI_COMPATIBLE_PROVIDERS)


def validate_body(body: Any) -> dict:
    if not isinstance(body, dict):
        raise bad_request("request body must be a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise bad_request("'model' must be a non-empty string")
    inp = body.get("input")
    if isinstance(inp, str):
        if not inp:
            raise bad_request("'input' must not be empty")
    elif isinstance(inp, list):
        if not inp:
            raise bad_request("'input' must not be empty")
    else:
        raise bad_request("'input' must be a string or an array")
    return body


def _ollama_payload(route: Route, body: dict) -> dict:
    inp = body["input"]
    payload: dict = {"model": route.upstream_model, "input": inp if isinstance(inp, list) else [inp]}
    if body.get("dimensions") is not None:
        payload["dimensions"] = body["dimensions"]
    return payload


def _from_ollama(data: dict, requested: str) -> dict | None:
    vectors = data.get("embeddings")
    if not isinstance(vectors, list):
        return None
    prompt_tokens = data.get("prompt_eval_count")
    prompt_tokens = int(prompt_tokens) if isinstance(prompt_tokens, int) else 0
    return {
        "object": "list",
        "model": requested,
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


def _usage(data: dict) -> Usage:
    u = data.get("usage")
    if isinstance(u, dict) and isinstance(u.get("prompt_tokens"), int):
        return Usage(input_tokens=int(u["prompt_tokens"]), output_tokens=0)
    return Usage(input_tokens=0, output_tokens=0, source=UNKNOWN)


async def _embed_one(route: Route, body: dict, requested: str, principal: str, state: Any) -> dict:
    api_key = await _forward.resolve_api_key(state, route.api_key_ref)
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    is_ollama = route.provider in _forward.OLLAMA_PROVIDERS
    if is_ollama:
        base = (route.api_base or "").rstrip("/")
        url = f"{base}/api/embed"
        payload = _ollama_payload(route, body)
    else:
        default = _forward.PROVIDER_DEFAULT_BASES.get(route.provider, _forward.DEFAULT_OPENAI_BASE)
        url = f"{(route.api_base or default).rstrip('/')}/embeddings"
        payload = dict(body)
        payload["model"] = route.upstream_model
    what = f"the backend for model {route.model_name!r}"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_forward.TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise upstream_error(f"{what} timed out") from None
    except httpx.HTTPError:
        raise upstream_error(f"{what} could not be reached") from None
    if is_ollama and resp.status_code == 404:
        # /api/embed missing, or the model not pulled: never a caller error.
        raise _forward._ollama_404_error(route, resp)
    err = _forward._status_error(resp, route, api_key)
    if err is not None:
        raise err
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise upstream_error(f"{what} returned a response that is not a JSON object")
    if is_ollama:
        data = _from_ollama(data, requested)
        if data is None:
            raise upstream_error(f"{what} returned no embeddings")

    usage = _usage(data)
    cost = cost_of(route.backend_type or route.backend_name, route.upstream_model, usage) if usage.known else Cost(
        None, False, "usage not reported by the backend")
    await _forward._record_trace(
        state, principal, route.upstream_model, usage, cost, route.backend_name,
        json.dumps(body), f"{len(data.get('data') or [])} embedding(s)",
        int((time.monotonic() - started) * 1000), "success", estimated=not usage.known,
    )
    if cost and cost.usd and cost.usd > 0:
        _forward._record_spend(state, principal, cost.usd)
    _forward._notify_lifecycle(state, route.backend_name)
    return data


async def create_embedding(routes: list[Route], body: dict, requested: str, principal: str,
                           state: Any) -> dict:
    """One embeddings call with the chat path's retry / failover."""
    if not routes:
        raise GatewayError(404, f"model {requested!r} not found", code="model_not_found")
    return await _forward._call_with_retry(
        routes, lambda route: _embed_one(route, body, requested, principal, state))
