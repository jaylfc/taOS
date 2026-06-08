"""OTLP/HTTP+JSON receiver — Phase 2.

Accepts ``POST /v1/traces`` with an OTLP ``ExportTraceServiceRequest`` JSON
body and writes each span to the per-agent ``SpanStore`` (keyed by the
``gen_ai.agent.name`` or ``service.name`` resource attribute; spans with
no agent land in ``_system``).

Design choices:
- HTTP/JSON only (no gRPC).  gRPC/:4317 is deferred per spec §7 footnote.
- Binds ``localhost`` only — never exposed externally.
- Port-busy is non-fatal; the emitter is fire-and-forget.
- Runs as an embedded FastAPI sub-application mounted inside the main app,
  started during the lifespan.  No separate process; no port fork.

Mounting strategy: the receiver is NOT a separate server on :4318.  Instead
it exposes a plain FastAPI router whose routes are registered directly on
the main app.  This keeps the architecture simple (one process, one port) and
avoids "localhost port-busy is non-fatal" complexity.  The emitter points to
the main app port (default 6969) for the ``/v1/traces`` path.  If a dedicated
OTLP port is ever needed (e.g. to accept from taOSmd on a different cluster
node), the receiver URL in the emitter config can be overridden.

Thread/process safety: ``SpanStoreRegistry`` uses ``asyncio.Lock``; writes are
idempotent (INSERT OR IGNORE on span_id PK).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.otel.span_store import SpanStoreRegistry

logger = logging.getLogger(__name__)

router = APIRouter()


def _attr_value(value_dict: dict) -> Any:
    """Extract the Python value from an OTLP AttributeValue dict.

    OTLP JSON encodes each value as ``{"stringValue": "…"}`` or
    ``{"intValue": "123"}`` etc.  This unpacks whichever key is present.
    """
    if not isinstance(value_dict, dict):
        return value_dict
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in value_dict:
            return value_dict[key]
    if "intValue" in value_dict:
        # OTLP encodes int64 as string in JSON to avoid precision loss.
        try:
            return int(value_dict["intValue"])
        except (TypeError, ValueError):
            return value_dict["intValue"]
    if "arrayValue" in value_dict:
        return [_attr_value(v) for v in value_dict["arrayValue"].get("values", [])]
    if "kvlistValue" in value_dict:
        out: dict[str, Any] = {}
        for kv in value_dict["kvlistValue"].get("values", []):
            if not isinstance(kv, dict):
                continue
            key = kv.get("key")
            if isinstance(key, str) and "value" in kv:
                out[key] = _attr_value(kv["value"])
        return out
    return None


def _parse_attributes(raw_attrs: list[dict]) -> dict[str, Any]:
    """Parse a list of OTLP KeyValue dicts into a plain Python dict."""
    result: dict[str, Any] = {}
    for kv in raw_attrs or []:
        key = kv.get("key", "")
        if key and "value" in kv:
            result[key] = _attr_value(kv["value"])
    return result


def _extract_agent_name(resource_attrs: dict, span_attrs: dict) -> str:
    """Resolve the agent name from resource or span attributes.

    Priority:
      1. ``gen_ai.agent.name`` from span attributes
      2. ``gen_ai.agent.name`` from resource attributes
      3. ``service.name`` from resource attributes
      4. ``_system`` fallback
    """
    return (
        span_attrs.get("gen_ai.agent.name")
        or resource_attrs.get("gen_ai.agent.name")
        or resource_attrs.get("service.name")
        or "_system"
    )


def _ns_to_int(value: Any) -> int:
    """OTLP encodes nanosecond timestamps as strings; coerce to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _ingest_export_request(body: dict, registry: SpanStoreRegistry) -> int:
    """Parse an ExportTraceServiceRequest and write all spans.

    Returns the number of spans written.
    """
    written = 0
    for resource_span in body.get("resourceSpans", []):
        resource = resource_span.get("resource", {})
        resource_attrs = _parse_attributes(resource.get("attributes", []))

        for scope_span in resource_span.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                span_attrs = _parse_attributes(span.get("attributes", []))
                agent_name = _extract_agent_name(resource_attrs, span_attrs)
                conversation_id = (
                    span_attrs.get("gen_ai.conversation.id")
                    or resource_attrs.get("gen_ai.conversation.id")
                )

                span_id = span.get("spanId") or ""
                if not span_id:
                    continue  # malformed span — skip

                trace_id = span.get("traceId") or ""
                parent_span_id = span.get("parentSpanId") or None
                name = span.get("name") or "unknown"
                start_ns = _ns_to_int(span.get("startTimeUnixNano", 0))
                end_ns = _ns_to_int(span.get("endTimeUnixNano", 0))
                status_block = span.get("status") or {}
                status_code = status_block.get("code") or None

                store = await registry.get(agent_name)
                await store.write_span(
                    span_id=span_id,
                    trace_id=trace_id,
                    parent_span_id=parent_span_id,
                    name=name,
                    start_time_ns=start_ns,
                    end_time_ns=end_ns,
                    attributes={**resource_attrs, **span_attrs},
                    status_code=str(status_code) if status_code else None,
                    agent_name=agent_name,
                    conversation_id=conversation_id,
                )
                written += 1

    return written


@router.post("/v1/traces")
async def receive_traces(request: Request):
    """OTLP/HTTP+JSON trace receiver.

    Accepts an ``ExportTraceServiceRequest`` JSON body.  Writes each span
    to the per-agent ``SpanStore`` via ``app.state.span_store_registry``.

    Returns OTLP ``ExportTraceServiceResponse`` shape (empty ``partialSuccess``
    means all spans were accepted).
    """
    client_host = getattr(request.client, "host", None)
    # None means in-process ASGI transport (tests / same-process calls) — allow.
    if client_host is not None and client_host not in {"127.0.0.1", "::1", "localhost"}:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    registry: SpanStoreRegistry | None = getattr(request.app.state, "span_store_registry", None)
    if registry is None:
        return JSONResponse(
            {"error": "span_store_registry not configured"},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception as exc:
        logger.warning("otel receiver: JSON parse error: %s", exc)
        return JSONResponse({"error": f"invalid JSON: {exc}"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid ExportTraceServiceRequest payload"}, status_code=400)

    try:
        written = await _ingest_export_request(body, registry)
    except ValueError as exc:
        logger.warning("otel receiver: bad request: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.exception("otel receiver: ingest error: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    logger.debug("otel receiver: accepted %d spans", written)
    # OTLP response: empty partialSuccess = all spans accepted
    return {"partialSuccess": {}}


def setup_receiver(app_state: Any, data_dir: Path) -> SpanStoreRegistry:
    """Create and attach a SpanStoreRegistry to app.state.

    Called from the app lifespan before the first request arrives.
    Returns the registry so the caller can store it.

    This is separate from the router registration (which happens at
    create_app time via register_all_routers) so the registry is always
    created even if the router cannot be added (e.g. port conflict scenarios).
    """
    registry = SpanStoreRegistry(data_dir)
    app_state.span_store_registry = registry
    logger.info("otel receiver: SpanStoreRegistry ready at %s/trace/", data_dir)
    return registry
