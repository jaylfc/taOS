"""OpenAI chat completions <-> Anthropic Messages API, for the taOS gateway.

Translates an OpenAI-shaped request into a Messages API request, and the
Messages API response (or its SSE event stream) back into OpenAI
``chat.completion`` / ``chat.completion.chunk`` objects. No SDK dependency.

The design follows aisuite (MIT, github.com/andrewyng/aisuite); no aisuite
code is copied here.

Response and stream shapes are Anthropic's documented ones:
https://platform.claude.com/docs/en/build-with-claude/streaming
https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncGenerator

import httpx

from tinyagentos.llm_gateway.errors import GatewayError, upstream_error, bad_request, rate_limit_error
from tinyagentos.llm_usage.usage import from_anthropic, AnthropicStreamUsage
from tinyagentos.llm_gateway.forward import _notify_lifecycle

ANTHROPIC_API_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024

logger = logging.getLogger(__name__)

# Anthropic stop_reason -> OpenAI finish_reason. Clients (PicoClaw among them)
# decide whether to run tools from finish_reason, so this is load-bearing.
_FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    "model_context_window_exceeded": "length",
}


def _finish_reason(stop_reason: str | None) -> str:
    """Map an Anthropic stop_reason; anything unmapped is "stop" and logged."""
    mapped = _FINISH_REASONS.get(stop_reason or "")
    if mapped is None:
        logger.warning(
            "llm_gateway: unmapped Anthropic stop_reason %r, reporting finish_reason 'stop'",
            stop_reason,
        )
        return "stop"
    return mapped


def _arguments(tool_input: Any) -> str:
    """Anthropic's tool ``input`` is an object; OpenAI's ``arguments`` is a JSON string."""
    return json.dumps(tool_input if tool_input is not None else {})


def _redact(text: str, *secrets: str | None) -> str:
    """Redact sensitive values from text."""
    for s in secrets:
        if s:
            text = text.replace(s, "[redacted]")
    return text


def _convert_openai_tool_to_anthropic(tool: dict) -> dict:
    fn = tool.get("function", {})
    return {
        "name": fn.get("name", ""),
        "description": fn.get("description", ""),
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


def _convert_tool_choice(tool_choice: dict | str | None) -> dict | str | None:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"type": "auto"}
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice == "none":
            return {"type": "none"}
        return tool_choice
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            fn = tool_choice.get("function", {})
            return {"type": "tool", "name": fn.get("name", "")}
    return tool_choice


def _messages_url(route: Any) -> str:
    base = (getattr(route, "api_base", None) or ANTHROPIC_API_BASE).rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/v1/messages"


async def _openai_to_anthropic(
    body: dict,
    principal: str,
    state: Any,
    api_key: str | None = None,
    route: Any = None,
    stream: bool = False,
) -> dict:
    """Convert OpenAI chat request to Anthropic Messages API request."""
    request = {}

    request["model"] = getattr(route, "upstream_model", "") if route else ""
    request.setdefault("model", body.get("model", ""))
    if stream:
        request["stream"] = True

    messages = body.get("messages", [])
    system_message = None
    anthropic_messages = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            system_message = content
        elif role == "user":
            anthropic_messages.append({"role": "user", "content": content or ""})
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                content_blocks = []
                if content:
                    content_blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    arguments = fn.get("arguments", "{}")
                    try:
                        parsed_input = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        parsed_input = {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": parsed_input,
                    })
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_messages.append({"role": "assistant", "content": content or ""})
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            tool_content = msg.get("content", "")
            anthropic_messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": tool_content}],
            })

    if system_message:
        request["system"] = system_message

    tools = body.get("tools")
    if tools:
        request["tools"] = [_convert_openai_tool_to_anthropic(t) for t in tools]
        tool_choice = _convert_tool_choice(body.get("tool_choice"))
        if tool_choice is not None and tool_choice != "none":
            request["tool_choice"] = tool_choice

    request["messages"] = anthropic_messages
    request["max_tokens"] = body.get("max_tokens", DEFAULT_MAX_TOKENS)

    for param in ["temperature", "top_p", "top_k", "stop_sequences"]:
        if param in body:
            request[param] = body[param]

    return request


async def _anthropic_status_error(
    resp: httpx.Response,
    api_key: str | None,
    api_key_for_redaction: str | None = None,
) -> GatewayError | None:
    """Map an upstream Anthropic response to a GatewayError, or None for 2xx."""
    status = resp.status_code
    if 200 <= status < 300:
        return None

    # Read body for streaming responses
    try:
        _ = resp.content
    except httpx.ResponseNotRead:
        try:
            await resp.aread()
        except Exception:
            pass

    error_msg = resp.text
    if resp.headers.get("content-type", "").startswith("application/json"):
        try:
            error_data = resp.json()
            error_msg = error_data.get("error", {}).get("message", resp.text)
        except Exception:
            pass

    redacted_msg = _redact(error_msg, api_key, api_key_for_redaction)

    if status == 429:
        retry_after = resp.headers.get("retry-after")
        return rate_limit_error(f"the Anthropic API failed (HTTP 429): {redacted_msg}", retry_after)

    if 500 <= status < 600 or status == 529:
        return upstream_error(f"the Anthropic API failed (HTTP {status}): {redacted_msg}")

    if status in {401, 403}:
        return upstream_error(f"the Anthropic API failed (HTTP {status}): {redacted_msg}")

    if status in {400, 404}:
        return bad_request(redacted_msg)

    return upstream_error(f"the Anthropic API failed (HTTP {status}): {redacted_msg}")


async def _call_anthropic(
    request: dict,
    api_key: str | None,
    route: Any = None,
    api_key_for_redaction: str | None = None,
) -> dict:
    """Make HTTP request to Anthropic API with proper error handling and redaction."""
    url = _messages_url(route)

    headers = {
        "content-type": "application/json",
        "x-api-key": api_key or "",
        "anthropic-version": ANTHROPIC_VERSION,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)) as client:
            resp = await client.post(url, json=request, headers=headers)
    except httpx.TimeoutException as exc:
        raise upstream_error(f"the Anthropic API timed out") from exc
    except httpx.HTTPError as exc:
        raise upstream_error(f"the Anthropic API could not be reached") from exc

    err = await _anthropic_status_error(resp, api_key, api_key_for_redaction)
    if err is not None:
        raise err

    try:
        data = resp.json()
    except ValueError:
        data = None

    if not isinstance(data, dict):
        raise upstream_error("the Anthropic API returned a response that is not a JSON object")

    return data


async def _anthropic_to_openai(response: dict, original_body: dict, api_key: str | None = None) -> dict:
    """Convert Anthropic Messages API response to OpenAI chat.completion format."""
    if "error" in response:
        error_msg = response["error"].get("message", "Unknown error")
        if api_key and api_key in error_msg:
            error_msg = error_msg.replace(api_key, "[redacted]")
        raise upstream_error(f"Anthropic API error: {error_msg}")

    # Build OpenAI response
    openai_response = {
        "id": response.get("id", f"chatcmpl-{hash(str(response))}"),
        "object": "chat.completion",
        "created": int(response.get("created", 0)),
        "model": original_body.get("model", "unknown"),
        "choices": [],
        "system_fingerprint": None
    }

    # Extract usage using from_anthropic which returns Usage.unknown() for missing data
    usage_obj = from_anthropic(response)

    if usage_obj.known:
        openai_response["usage"] = {
            "prompt_tokens": usage_obj.input_tokens,
            "completion_tokens": usage_obj.output_tokens,
            "total_tokens": usage_obj.input_tokens + usage_obj.output_tokens,
        }
    # tool_use blocks ({"type": "tool_use", "id", "name", "input": {...}})
    # become ``tool_calls``. OpenAI allows both on one message, so text Claude
    # writes before a call is kept.
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in response.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id") or f"call_{len(tool_calls) + 1}",
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": _arguments(block.get("input")),
                },
            })
    content = "".join(text_parts) if text_parts else None

    # Build choices
    choice = {
        "index": 0,
        "message": {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls or None
        },
        "finish_reason": _finish_reason(response.get("stop_reason")),
        "logprobs": None
    }
    openai_response["choices"].append(choice)

    return openai_response


class _StreamTranslator:
    """Anthropic stream events -> OpenAI ``chat.completion.chunk`` objects.

    Event flow (streaming doc): message_start, then per content block a
    content_block_start, content_block_delta(s) and content_block_stop, then
    message_delta (``delta.stop_reason`` and a TOP-LEVEL ``usage``), then
    message_stop. ping and unknown events are ignored.
    """

    def __init__(self, model: str):
        self.model = model
        self.id = "chatcmpl-anthropic"
        self.created = int(time.time())
        self.usage_tracker = AnthropicStreamUsage()
        # Anthropic block index -> 0-based OpenAI tool_calls index.
        self.tool_index: dict[int, int] = {}
        self.done = False

    def _chunk(self, delta: dict, finish_reason: str | None = None) -> dict:
        return {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    def feed(self, event: dict) -> list[dict]:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") or {}
            if message.get("id"):
                self.id = message["id"]
            self.usage_tracker.feed(event)
            return [self._chunk({"role": "assistant", "content": ""})]

        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "text" and block.get("text"):
                return [self._chunk({"content": block["text"]})]
            if block.get("type") == "tool_use":
                idx = len(self.tool_index)
                self.tool_index[event.get("index", idx)] = idx
                return [self._chunk({"tool_calls": [{
                    "index": idx,
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {"name": block.get("name", ""), "arguments": ""},
                }]})]
            return []

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                return [self._chunk({"content": delta.get("text", "")})]
            if delta.get("type") == "input_json_delta":
                partial = delta.get("partial_json", "")
                idx = self.tool_index.get(event.get("index"))
                if idx is None or not partial:
                    return []
                # Raw partial JSON text, passed through as a string piece:
                # the pieces concatenate to the JSON of the tool's input.
                return [self._chunk({"tool_calls": [{
                    "index": idx, "function": {"arguments": partial},
                }]})]
            return []

        if kind == "message_delta":
            out: list[dict] = []
            stop_reason = (event.get("delta") or {}).get("stop_reason")
            if stop_reason is not None:
                out.append(self._chunk({}, _finish_reason(stop_reason)))
            usage = event.get("usage")
            if isinstance(usage, dict):
                self.usage_tracker.feed(event)
                usage_obj = self.usage_tracker.result()
                out.append({
                    "id": self.id,
                    "object": "chat.completion.chunk",
                    "created": self.created,
                    "model": self.model,
                    "choices": [],
                    "usage": {
                        "prompt_tokens": usage_obj.input_tokens,
                        "completion_tokens": usage_obj.output_tokens,
                        "total_tokens": usage_obj.input_tokens + usage_obj.output_tokens,
                    },
                })
            return out

        if kind == "message_stop":
            self.done = True
            return []

        if kind == "error":
            error = event.get("error") or {}
            return [{"error": {
                "message": error.get("message", "Anthropic stream error"),
                "type": error.get("type", "api_error"),
            }}]

        return []


def _sse_data(frame: str) -> str | None:
    """The joined ``data:`` payload of one SSE frame (``event:`` lines skipped)."""
    lines = [
        line[5:].lstrip(" ") for line in frame.split("\n") if line.startswith("data:")
    ]
    return "\n".join(lines) if lines else None


async def chat_completion_anthropic(
    routes: list[Any],
    body: dict,
    api_key: str | None,
    principal: str,
    state: Any,
) -> dict:
    """Translate OpenAI request to Anthropic and back (non-streaming) with failover."""
    from tinyagentos.llm_gateway.forward import _call_with_retry, _record_trace, _record_spend, _conservative_budget_estimate, resolve_api_key
    from tinyagentos.llm_usage.pricing import Cost, cost_of
    from tinyagentos.llm_usage.usage import from_anthropic

    anthropic_routes = [r for r in routes if getattr(r, "provider", None) == "anthropic"]
    if not anthropic_routes:
        raise upstream_error("no Anthropic backends available")

    async def _call_one(route: Any) -> dict:
        api_key = await resolve_api_key(state, route.api_key_ref)
        anthropic_request = await _openai_to_anthropic(body, principal, state, api_key, route=route, stream=False)
        response = await _call_anthropic(anthropic_request, api_key, route=route)

        request_text = json.dumps(body)
        response_text = json.dumps(response)
        usage_obj = from_anthropic(response)

        if not usage_obj.known:
            cost = Cost(None, False, "usage not reported by the backend")
            await _record_trace(
                state, principal, response.get("model", ""), usage_obj, cost,
                route.backend_name,
                request_text, response_text, 0, "success", estimated=True,
            )
            estimate = _conservative_budget_estimate(
                route.backend_type or route.backend_name,
                response.get("model", ""),
                request_text, response_text,
            )
            if estimate > 0:
                _record_spend(state, principal, estimate)
        else:
            cost = cost_of(route.backend_type or route.backend_name,
                           response.get("model", ""), usage_obj)
            await _record_trace(
                state, principal, response.get("model", ""), usage_obj, cost,
                route.backend_name,
                request_text, response_text, 0, "success", estimated=False,
            )
            if cost and cost.usd and cost.usd > 0:
                _record_spend(state, principal, cost.usd)

        _notify_lifecycle(state, route.backend_name)
        return response

    response = await _call_with_retry(anthropic_routes, _call_one)
    result = await _anthropic_to_openai(response, body)
    return result


async def chat_completion_stream_anthropic(
    routes: list[Any],
    body: dict,
    principal: str,
    state: Any,
) -> AsyncGenerator[bytes, None]:
    """Translate OpenAI streaming request to Anthropic and stream back with failover."""
    from tinyagentos.llm_gateway.forward import _stream_with_retry, _record_trace, _record_spend, _conservative_budget_estimate, resolve_api_key
    from tinyagentos.llm_usage.pricing import Cost, cost_of
    from tinyagentos.llm_usage.usage import Usage

    anthropic_routes = [r for r in routes if getattr(r, "provider", None) == "anthropic"]
    if not anthropic_routes:
        raise upstream_error("no Anthropic backends available for streaming")

    async def _stream_one(route: Any) -> AsyncGenerator[bytes, None]:
        api_key = await resolve_api_key(state, route.api_key_ref)
        anthropic_request = await _openai_to_anthropic(body, principal, state, api_key, route=route, stream=True)
        translator = _StreamTranslator(body.get("model", "unknown"))
        completion_text: list[str] = []
        request_text = json.dumps(body)
        stream_done = False
        # Spend is settled exactly once, from a finally, on every way out of
        # the stream: normal end, client abort (GeneratorExit), cancellation
        # (CancelledError) or a mid-stream upstream error. An aborted or
        # errored stream is still charged, or aborting would dodge the cap.
        delta_usage_seen = False
        upstream_failed = False
        settled = False

        async def _settle() -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            response_text = "".join(completion_text)
            usage_obj: Usage = translator.usage_tracker.result()
            # message_stop seen: the answer finished, whether or not the
            # client stayed for the final [DONE].
            finished = translator.done
            status = "success" if finished else "failure"
            # message_start alone reports an initial output count, not what
            # was generated: an unfinished stream counts as real usage only
            # once a message_delta usage arrived, else it is estimated.
            real_usage = usage_obj.known and (finished or delta_usage_seen)

            if not real_usage:
                cost = Cost(None, False, "usage not reported by the backend")
                await _record_trace(
                    state, principal, body.get("model", ""), usage_obj, cost,
                    route.backend_name,
                    request_text, response_text, 0, status, estimated=True,
                )
                estimate = _conservative_budget_estimate(
                    route.backend_type or route.backend_name,
                    body.get("model", ""),
                    request_text, response_text,
                )
                if estimate > 0:
                    _record_spend(state, principal, estimate)
            else:
                cost = cost_of(route.backend_type or route.backend_name,
                               body.get("model", ""), usage_obj)
                await _record_trace(
                    state, principal, body.get("model", ""), usage_obj, cost,
                    route.backend_name,
                    request_text, response_text, 0, status, estimated=False,
                )
                if cost and cost.usd and cost.usd > 0:
                    _record_spend(state, principal, cost.usd)

            if not upstream_failed:
                _notify_lifecycle(state, route.backend_name)

        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)) as client:
            req = client.build_request(
                "POST",
                _messages_url(route),
                json=anthropic_request,
                headers={
                    "content-type": "application/json",
                    "x-api-key": api_key or "",
                    "anthropic-version": ANTHROPIC_VERSION,
                },
            )
            resp = await client.send(req, stream=True)
            err = await _anthropic_status_error(resp, api_key)
            if err is not None:
                raise err
            try:
                _buf = ""
                async for raw in resp.aiter_text():
                    if stream_done:
                        break
                    if not raw:
                        continue
                    _buf += raw.replace("\r\n", "\n")
                    while True:
                        if translator.done:
                            yield b"data: [DONE]\n\n"
                            stream_done = True
                            break
                        idx = _buf.find("\n\n")
                        if idx < 0:
                            break
                        frame, _buf = _buf[:idx], _buf[idx + 2:]
                        data = _sse_data(frame)
                        if not data:
                            continue
                        try:
                            event = json.loads(data)
                        except ValueError:
                            logger.debug("llm_gateway: skipped a non-JSON Anthropic SSE frame")
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") == "message_delta" and isinstance(event.get("usage"), dict):
                            delta_usage_seen = True
                        for chunk in translator.feed(event):
                            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                completion_text.append(delta.get("text", ""))
            except Exception:
                upstream_failed = True
                raise
            finally:
                try:
                    await resp.aclose()
                finally:
                    await _settle()

    outer = _stream_with_retry(anthropic_routes, _stream_one)
    try:
        async for chunk in outer:
            yield chunk
    finally:
        # A client abort closes THIS generator: close the retry loop with it so
        # the attempt's accounting runs before aclose() returns, not at gc.
        await outer.aclose()
