"""One non-streaming POST or SSE stream to an OpenAI-compatible backend.

The request body is forwarded verbatim (``tools``, ``tool_choice``,
``tool_calls`` and any vendor fields included); only ``model`` is rewritten
to the backend's own id. Upstream failures become OpenAI-shaped errors, and
neither the backend's key nor its URL is ever put in a response or a log.

For streams, ``stream_options: {include_usage: true}`` is injected unless the
caller set it, and the final usage-only chunk is stripped. When the upstream
returns no usage, the call is recorded as UNKNOWN and a conservative budget
estimate is used.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from tinyagentos.llm_gateway.errors import GatewayError, upstream_error
from tinyagentos.llm_gateway.resolve import Route
from tinyagentos.llm_usage.pricing import Cost, cost_of, find_price, price_usage
from tinyagentos.llm_usage.usage import UNKNOWN, OpenAIStreamUsage, Usage, ensure_stream_usage, from_openai

logger = logging.getLogger(__name__)

# LiteLLM's own default for an ``openai/`` entry with no api_base.
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
_ENV_REF = "os.environ/"
# Matches the LiteLLM router's 120s request timeout; connect fails fast.
TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)
# Upstream 4xx that describe the caller's request; passed through (redacted).
# Anything else (401/403/404: taOS's own key or config is wrong) is a 502.
_CALLER_4XX = {400, 409, 413, 422, 429}

_budget_store_cache: Any = None
_budget_store_path_cache: str = ""


def _budget_store_for(path: str) -> Any:
    global _budget_store_cache, _budget_store_path_cache
    if _budget_store_cache is None or _budget_store_path_cache != path:
        from tinyagentos.agent_budget_store import AgentBudgetStore
        _budget_store_cache = AgentBudgetStore(path)
        _budget_store_path_cache = path
    return _budget_store_cache


def _record_spend(state: Any, principal: str, cost_usd: float) -> None:
    if cost_usd is None or cost_usd <= 0:
        return
    try:
        data_dir = getattr(state, "data_dir", None)
        if data_dir is None:
            return
        budget_path = str(Path(data_dir) / ".agent_budgets.db")
        _budget_store_for(budget_path).add_spend(principal, cost_usd)
    except Exception:  # noqa: BLE001 - never let the store's error raise into the caller
        logger.warning("llm_gateway: budget spend failed", exc_info=True)


def _conservative_budget_estimate(backend_type: str, model: str, request_text: str, response_text: str) -> float:
    prompt_tokens = max(1, len(request_text) // 4)
    completion_tokens = max(1, len(response_text) // 4)
    found = find_price(backend_type, model)
    if found:
        key, entry = found
        usage_est = Usage(input_tokens=prompt_tokens, output_tokens=completion_tokens, source=UNKNOWN)
        return price_usage(entry, usage_est)
    return max(0.0001, (prompt_tokens + completion_tokens) * 0.00001)


async def _record_trace(
    state: Any,
    principal: str,
    model: str,
    usage: Usage,
    cost: Cost,
    backend_type: str,
    request_text: str,
    response_text: str,
    duration_ms: int,
    status: str,
    estimated: bool = False,
) -> None:
    registry = getattr(state, "trace_registry", None)
    if registry is None:
        return
    try:
        store = await registry.get(principal)
        payload: dict[str, Any] = {
            "status": status,
            "messages": [],
            "response": response_text,
            "metadata": {"model": model, "backend_name": backend_type},
        }
        if estimated:
            payload["usage_estimated"] = True
        await store.record(
            kind="llm_call",
            agent_name=principal,
            model=model,
            backend_name=backend_type,
            duration_ms=duration_ms,
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cost_usd=cost.usd if cost and cost.usd is not None else None,
            payload=payload,
        )
    except Exception:  # noqa: BLE001 - never let the store's error raise into the caller
        logger.warning("llm_gateway: trace record failed", exc_info=True)


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


def chat_completion_stream(
    route: Route,
    body: dict,
    api_key: str | None,
    principal: str,
    state: Any,
) -> Any:
    from fastapi.responses import StreamingResponse

    url = f"{(route.api_base or DEFAULT_OPENAI_BASE).rstrip('/')}/chat/completions"
    payload = dict(body)
    payload["model"] = route.upstream_model

    caller_asked_for_usage = False
    stream_opts = body.get("stream_options")
    if isinstance(stream_opts, dict):
        caller_asked_for_usage = bool(stream_opts.get("include_usage"))

    if not caller_asked_for_usage:
        payload = dict(ensure_stream_usage(body))
        payload["model"] = route.upstream_model

    headers = {"content-type": "application/json"}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    request_text = json.dumps(body)

    async def _event_stream():
        usage_tracker = OpenAIStreamUsage()
        completion_text: list[str] = []
        _buf = b""
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            try:
                req = client.build_request("POST", url, json=payload, headers=headers)
                upstream_resp = await client.send(req, stream=True)
            except httpx.TimeoutException:
                raise upstream_error("the backend timed out") from None
            except httpx.HTTPError:
                raise upstream_error("the backend could not be reached") from None
            try:
                async for raw in upstream_resp.aiter_raw():
                    if not raw:
                        continue
                    _buf += raw
                    while True:
                        idx = _buf.find(b"\n\n")
                        if idx < 0:
                            break
                        msg = _buf[:idx]
                        _buf = _buf[idx + 2:]
                        msg_str = msg.decode("utf-8", errors="replace").strip()
                        if not msg_str:
                            continue
                        data = msg_str[5:].strip() if msg_str.startswith("data: ") else msg_str
                        if data == "[DONE]":
                            yield (msg_str + "\n\n").encode("utf-8")
                            continue
                        try:
                            chunk = json.loads(data)
                        except ValueError:
                            yield (msg_str + "\n\n").encode("utf-8")
                            continue
                        is_usage_only = isinstance(chunk, dict) and isinstance(chunk.get("usage"), dict) and not chunk.get("choices")
                        if caller_asked_for_usage or not is_usage_only:
                            yield (msg_str + "\n\n").encode("utf-8")
                        if is_usage_only or caller_asked_for_usage:
                            usage_tracker.feed(chunk)
                        if not is_usage_only:
                            for choice in chunk.get("choices", []):
                                delta = choice.get("delta", {})
                                content = delta.get("content")
                                if isinstance(content, str):
                                    completion_text.append(content)
            finally:
                await upstream_resp.aclose()

        usage = usage_tracker.result()
        response_text = "".join(completion_text)
        if not usage.known:
            cost = Cost(None, False, "usage not reported by the backend")
            await _record_trace(state, principal, route.upstream_model, usage, cost, route.backend_name, request_text, response_text, 0, "success", estimated=True)
            estimate = _conservative_budget_estimate(route.backend_name, route.upstream_model, request_text, response_text)
            if estimate > 0:
                _record_spend(state, principal, estimate)
        else:
            cost = cost_of(route.backend_name, route.upstream_model, usage)
            await _record_trace(state, principal, route.upstream_model, usage, cost, route.backend_name, request_text, response_text, 0, "success", estimated=False)
            if cost and cost.usd and cost.usd > 0:
                _record_spend(state, principal, cost.usd)

    return StreamingResponse(_event_stream(), media_type="text/event-stream", headers={"cache-control": "no-cache"})


async def chat_completion(route: Route, body: dict, api_key: str | None, principal: str, state: Any) -> dict:
    """One non-streaming POST to an OpenAI-compatible backend.

    The request body is forwarded verbatim (``tools``, ``tool_choice``,
    ``tool_calls`` and any vendor fields included); only ``model`` is rewritten
    to the backend's own id. Upstream failures become OpenAI-shaped errors, and
    neither the backend's key nor its URL is ever put in a response or a log.
    """
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

    request_text = json.dumps(body)
    response_text = json.dumps(data)
    usage = from_openai(data)
    if not usage.known:
        cost = Cost(None, False, "usage not reported by the backend")
        await _record_trace(state, principal, route.upstream_model, usage, cost, route.backend_name, request_text, response_text, 0, "success", estimated=True)
        estimate = _conservative_budget_estimate(route.backend_name, route.upstream_model, request_text, response_text)
        if estimate > 0:
            _record_spend(state, principal, estimate)
    else:
        cost = cost_of(route.backend_name, route.upstream_model, usage)
        await _record_trace(state, principal, route.upstream_model, usage, cost, route.backend_name, request_text, response_text, 0, "success", estimated=False)
        if cost and cost.usd and cost.usd > 0:
            _record_spend(state, principal, cost.usd)
    return data
