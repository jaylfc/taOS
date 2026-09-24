"""One non-streaming POST to an OpenAI-compatible backend.

The request body is forwarded verbatim (``tools``, ``tool_choice``,
``tool_calls`` and any vendor fields included); only ``model`` is rewritten
to the backend's own id. Upstream failures become OpenAI-shaped errors, and
neither the backend's key nor its URL is ever put in a response or a log.
"""
from __future__ import annotations

import logging
import os

import httpx

from tinyagentos.llm_gateway.errors import GatewayError, upstream_error
from tinyagentos.llm_gateway.resolve import Route

logger = logging.getLogger(__name__)

# LiteLLM's own default for an ``openai/`` entry with no api_base.
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
_ENV_REF = "os.environ/"
# Matches the LiteLLM router's 120s request timeout; connect fails fast.
TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
# Upstream 4xx that describe the caller's request; passed through (redacted).
# Anything else (401/403/404: taOS's own key or config is wrong) is a 502.
_CALLER_4XX = {400, 409, 413, 422, 429}


async def resolve_api_key(state, ref: str | None) -> str | None:
    """A literal key, or ``os.environ/<name>`` looked up in the secrets store
    (where the LiteLLM path gets it) and then the process environment."""
    if not ref:
        return None
    if not ref.startswith(_ENV_REF):
        return ref
    name = ref[len(_ENV_REF):]
    store = getattr(state, "secrets", None)
    if store is not None:
        try:
            rec = await store.get(name)
        except Exception:  # noqa: BLE001 - never let the store's error text out
            logger.warning("llm_gateway: secret lookup failed for a backend key")
            rec = None
        if rec and rec.get("value"):
            return rec["value"]
    return os.environ.get(name) or None


def _redact(text: str, *secrets: str | None) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text


def _caller_error(resp: httpx.Response, route: Route, api_key: str | None) -> GatewayError:
    status = resp.status_code
    message, type_, code = f"the backend rejected the request (HTTP {status})", "invalid_request_error", None
    try:
        err = resp.json().get("error")
    except Exception:  # noqa: BLE001 - not JSON, or not an object
        err = None
    if isinstance(err, dict):
        if isinstance(err.get("message"), str) and err["message"]:
            message = err["message"]
        if isinstance(err.get("type"), str) and err["type"]:
            type_ = err["type"]
        if isinstance(err.get("code"), str):
            code = err["code"]
    elif isinstance(err, str) and err:
        message = err
    return GatewayError(status, _redact(message, api_key, route.api_base), type=type_, code=code)


async def chat_completion(route: Route, body: dict, api_key: str | None) -> dict:
    url = f"{(route.api_base or DEFAULT_OPENAI_BASE).rstrip('/')}/chat/completions"
    payload = dict(body)
    payload["model"] = route.upstream_model
    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    what = f"the backend for model {route.model_name!r}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning("llm_gateway: backend %r timed out (%s) for model %r",
                       route.backend_name, type(exc).__name__, route.model_name)
        raise upstream_error(f"{what} timed out") from None
    except httpx.HTTPError as exc:
        logger.warning("llm_gateway: backend %r unreachable (%s) for model %r",
                       route.backend_name, type(exc).__name__, route.model_name)
        raise upstream_error(f"{what} could not be reached") from None

    status = resp.status_code
    if status in _CALLER_4XX:
        raise _caller_error(resp, route, api_key)
    if not 200 <= status < 300:
        logger.warning("llm_gateway: backend %r answered HTTP %d for model %r",
                       route.backend_name, status, route.model_name)
        raise upstream_error(f"{what} failed (HTTP {status})")
    try:
        data = resp.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        logger.warning("llm_gateway: backend %r returned a non-JSON-object body for model %r",
                       route.backend_name, route.model_name)
        raise upstream_error(f"{what} returned a response that is not a JSON object")
    return data
