"""Thin OTel emitter — Phase 2.

Converts a v1 trace envelope into an OTLP/HTTP+JSON span and POSTs it to the
local receiver (POST /v1/traces).  Mapping follows the semconv Part A contract:

    llm_call        → gen_ai.chat  (CLIENT span)
    tool_call       → execute_tool (INTERNAL span, opened)
    tool_result     → execute_tool (INTERNAL span, closed — same or linked)
    message_in/out  → events on the turn span (no standalone spans; §resolved 3)
    reasoning       → event gen_ai.reasoning on the turn span (§resolved 2)
    error           → exception event + ERROR status on the active span
    lifecycle       → agent.{event} (INTERNAL span)
    reasoning_audit → not emitted as a span (internal eval artifact per spec §4.7)

Fire-and-forget: the HTTP POST runs in the background.  If the receiver is
unreachable the span is silently dropped — the AgentTraceStore holds the
authoritative data.

The emitter is injected at startup as ``app.state.otel_emitter``.
``None`` means "no-op"; ``record()`` guards on that.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any

import httpx

from tinyagentos.otel.trace_utils import make_trace_id as _make_trace_id

logger = logging.getLogger(__name__)

# OTLP span kind constants (protobuf enum values used as integers in JSON)
_SPAN_KIND_INTERNAL = 1
_SPAN_KIND_CLIENT = 3

# Status codes
_STATUS_OK = "STATUS_CODE_OK"
_STATUS_ERROR = "STATUS_CODE_ERROR"
_STATUS_UNSET = "STATUS_CODE_UNSET"


def _make_span_id() -> str:
    """Mint a random 64-bit OTel spanId (16 hex chars)."""
    return secrets.token_bytes(8).hex()


def _ns_from_envelope(env: dict, *, use_end: bool = False) -> int:
    """Return nanosecond timestamp from envelope fields.

    start = created_at (seconds) converted to nanoseconds.
    end   = created_at + duration_ms (or created_at + 0 if absent).
    """
    created_at: float = env.get("created_at") or time.time()
    start_ns = int(created_at * 1_000_000_000)
    if use_end:
        duration_ms: int = env.get("duration_ms") or 0
        return start_ns + int(duration_ms * 1_000_000)
    return start_ns


def _kv(key: str, value: Any) -> dict:
    """Build an OTLP KeyValue for a string-typed value (most attributes)."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def _attrs(*pairs: tuple[str, Any]) -> list[dict]:
    """Build a list of OTLP KeyValues, skipping None values."""
    return [_kv(k, v) for k, v in pairs if v is not None]


def _span_event(name: str, time_unix_ns: int, attrs: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "timeUnixNano": str(time_unix_ns),
        "attributes": attrs or [],
    }


def _build_otlp_span(env: dict) -> dict | None:  # noqa: C901  (complexity is mapping logic)
    """Map a v1 envelope to a single OTLP span dict.

    Returns None for kinds that must not produce spans (reasoning_audit).
    """
    kind = env.get("kind", "")
    agent_name = env.get("agent_name") or "_system"
    payload: dict = env.get("payload") or {}

    # Stable conversation/trace ids
    conversation_id = env.get("thread_id") or env.get("trace_id")
    trace_id = _make_trace_id(conversation_id)
    span_id = _make_span_id()

    # Parent span id (from envelope parent_id; we treat it as a spanId hint)
    parent_span_id: str | None = None
    if env.get("parent_id"):
        # parent_id is our internal uuid; we derive an OTel spanId from it
        # so the hierarchy is at least hinted in the OTel trace even though
        # we don't hold full context state in the envelope.
        parent_span_id = hashlib.sha256(
            env["parent_id"].encode()
        ).digest()[:8].hex()

    start_ns = _ns_from_envelope(env, use_end=False)
    end_ns = _ns_from_envelope(env, use_end=True)

    events: list[dict] = []
    attributes: list[dict] = []
    status: dict = {"code": _STATUS_UNSET}
    span_name: str = "agent.event"
    span_kind: int = _SPAN_KIND_INTERNAL

    if kind == "llm_call":
        model = env.get("model") or payload.get("model") or ""
        provider = env.get("backend_name") or payload.get("provider") or payload.get("metadata", {}).get("provider") or ""
        span_name = f"chat {model}" if model else "chat"
        span_kind = _SPAN_KIND_CLIENT
        attributes = _attrs(
            ("gen_ai.operation.name", "chat"),
            ("gen_ai.system", provider or "unknown"),
            ("gen_ai.provider.name", provider or None),
            ("gen_ai.request.model", model or None),
            ("gen_ai.usage.input_tokens", env.get("tokens_in")),
            ("gen_ai.usage.output_tokens", env.get("tokens_out")),
            ("gen_ai.conversation.id", conversation_id),
            ("gen_ai.agent.name", agent_name),
        )
        # Finish reasons from response metadata
        finish_reasons = payload.get("metadata", {}).get("finish_reason")
        if finish_reasons:
            attributes.append(_kv("gen_ai.response.finish_reasons", str(finish_reasons)))
        # Input event (messages)
        messages = payload.get("messages")
        if messages is not None:
            events.append(_span_event(
                "gen_ai.content.prompt",
                start_ns,
                [_kv("gen_ai.prompt", json.dumps(messages, default=str)[:8192])],
            ))
        # Output event (response)
        response = payload.get("response")
        if response is not None:
            events.append(_span_event(
                "gen_ai.content.completion",
                end_ns,
                [_kv("gen_ai.completion", json.dumps(response, default=str)[:8192])],
            ))
        pstatus = payload.get("status", "")
        if pstatus == "failure":
            status = {"code": _STATUS_ERROR, "message": "llm_call failed"}
        else:
            status = {"code": _STATUS_OK}

    elif kind in ("tool_call", "tool_result"):
        tool_name = payload.get("tool") or ""
        span_name = f"execute_tool {tool_name}" if tool_name else "execute_tool"
        attributes = _attrs(
            ("gen_ai.operation.name", "execute_tool"),
            ("gen_ai.tool.name", tool_name or None),
            ("gen_ai.tool.call.id", env.get("id")),
            ("gen_ai.agent.name", agent_name),
            ("gen_ai.conversation.id", conversation_id),
        )
        if kind == "tool_call":
            args = payload.get("args")
            if args is not None:
                attributes.append(_kv("gen_ai.tool.call.arguments", json.dumps(args, default=str)[:4096]))
            if payload.get("caller"):
                attributes.append(_kv("caller", payload["caller"]))
        elif kind == "tool_result":
            result = payload.get("result")
            if result is not None:
                attributes.append(_kv("gen_ai.tool.call.result", json.dumps(result, default=str)[:4096]))
            success = payload.get("success")
            if success is False:
                status = {"code": _STATUS_ERROR, "message": "tool_result: success=false"}
            else:
                status = {"code": _STATUS_OK}

    elif kind in ("message_in", "message_out"):
        # Per resolved decision §3: message_in/out are events on the turn span,
        # not standalone spans.  We still emit them as minimal spans here so they
        # appear in the span store; a future Phase 3 viewer may fold them into
        # their parent turn span instead.
        role = "user" if kind == "message_in" else "assistant"
        span_name = "chat.message"
        attributes = _attrs(
            ("gen_ai.message.role", role),
            ("gen_ai.conversation.id", conversation_id),
            ("gen_ai.agent.name", agent_name),
        )
        content_key = "text" if kind == "message_in" else "content"
        content = payload.get(content_key, "")
        if content:
            events.append(_span_event(
                "gen_ai.content.prompt" if kind == "message_in" else "gen_ai.content.completion",
                start_ns,
                [_kv("gen_ai.message.role", role),
                 _kv("body", str(content)[:4096])],
            ))

    elif kind == "reasoning":
        # Per resolved decision §2: reasoning is a child event on the turn span.
        # We emit a minimal span that carries it so it's queryable.
        span_name = "gen_ai.reasoning"
        attributes = _attrs(
            ("gen_ai.agent.name", agent_name),
            ("gen_ai.conversation.id", conversation_id),
        )
        text = payload.get("text", "")
        if text:
            events.append(_span_event(
                "gen_ai.reasoning",
                start_ns,
                [_kv("reasoning.text", str(text)[:8192])],
            ))

    elif kind == "error":
        span_name = "agent.error"
        attributes = _attrs(
            ("gen_ai.agent.name", agent_name),
            ("gen_ai.conversation.id", conversation_id),
        )
        events.append(_span_event(
            "exception",
            start_ns,
            _attrs(
                ("exception.type", payload.get("stage")),
                ("exception.message", payload.get("message") or env.get("error")),
                ("exception.stacktrace", payload.get("traceback")),
            ),
        ))
        status = {"code": _STATUS_ERROR, "message": payload.get("message") or ""}

    elif kind == "lifecycle":
        event = payload.get("event", "")
        span_name = f"agent.{event}" if event else "agent.event"
        attributes = _attrs(
            ("gen_ai.agent.name", agent_name),
            ("gen_ai.conversation.id", conversation_id),
            ("lifecycle.event", event or None),
            ("lifecycle.reason", payload.get("reason") or None),
        )

    elif kind == "reasoning_audit":
        # Internal eval artifact — never emitted as an OTel span (spec §4.7).
        return None

    else:
        # Unknown kind — emit a generic span so nothing is silently lost.
        span_name = f"agent.{kind}"
        attributes = _attrs(
            ("gen_ai.agent.name", agent_name),
            ("gen_ai.conversation.id", conversation_id),
        )

    span: dict = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": span_name,
        "kind": span_kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attributes,
        "events": events,
        "status": status,
    }
    if parent_span_id:
        span["parentSpanId"] = parent_span_id

    return span


def _build_otlp_request(env: dict, span: dict) -> dict:
    """Wrap a single span in an ExportTraceServiceRequest envelope."""
    agent_name = env.get("agent_name") or "_system"
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": _attrs(
                        ("service.name", "taos"),
                        ("gen_ai.agent.name", agent_name),
                    )
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "tinyagentos.otel.emitter", "version": "2"},
                        "spans": [span],
                    }
                ],
            }
        ]
    }


class OTelEmitter:
    """Fire-and-forget OTel span emitter.

    Converts a v1 envelope to an OTLP span and POSTs it to the local
    receiver in a background task.  All errors are swallowed — the
    AgentTraceStore is the authoritative store.

    Args:
        receiver_url: Base URL of the OTLP/HTTP receiver, e.g.
                      ``http://localhost:4318``.  ``None`` disables emission.
    """

    def __init__(self, receiver_url: str | None = "http://localhost:4318"):
        self._receiver_url = receiver_url
        self._client: httpx.AsyncClient | None = None
        self._pending_tasks: set[asyncio.Task] = set()
        self._max_pending_tasks = 512

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=5.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    def emit(self, envelope: dict) -> None:
        """Schedule fire-and-forget emission of one envelope.

        No-op if receiver_url is not configured or no running event loop.
        """
        if not self._receiver_url:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if len(self._pending_tasks) >= self._max_pending_tasks:
            return
        task = loop.create_task(self._emit_async(envelope))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _emit_async(self, envelope: dict) -> None:
        """Build the OTLP span and POST it.  All errors are swallowed."""
        try:
            span = _build_otlp_span(envelope)
            if span is None:
                # reasoning_audit and other non-emitting kinds
                return
            payload = _build_otlp_request(envelope, span)
            client = self._get_client()
            url = self._receiver_url.rstrip("/") + "/v1/traces"
            await client.post(
                url,
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
        except Exception:
            # Fire-and-forget: any network / parsing failure is silent.
            pass
