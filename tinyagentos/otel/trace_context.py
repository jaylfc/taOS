"""W3C trace-context header builder — Phase 2 §4.4.

Builds the ``traceparent`` and ``X-TaOS-Conversation-Id`` headers that taOS
injects on every outbound memory call into qmd / taosmd so those backends can
nest their spans under the calling agent's OTel trace.

Contract (from taosmd/docs/otel-genai-mapping.md "Trace context propagation"):
- ``traceparent: 00-{traceId}-{spanId}-01``
  W3C Trace Context v1.  traceId = 32 hex chars (128-bit), spanId = 16 hex
  chars (64-bit), flags = 01 (sampled).
- ``X-TaOS-Conversation-Id: {conversation_id}``
  Passes the stable conversation thread id so the receiving side can attach
  ``gen_ai.conversation.id`` to its spans without parsing traceparent.

The traceId is derived deterministically from the conversation_id (SHA-256,
first 16 bytes) so the same conversation always maps to the same OTel trace,
matching the emitter's ``_make_trace_id()`` logic.  The spanId is a fresh
random 8 bytes per call (each memory call is a distinct span).

When ``conversation_id`` is None (e.g. non-contextual maintenance calls), a
random traceId is minted to maintain W3C header validity without implying a
correlation that does not exist.
"""
from __future__ import annotations

import hashlib
import secrets


def _make_trace_id(conversation_id: str | None) -> str:
    """32-char hex traceId.  Deterministic if conversation_id is set."""
    if conversation_id:
        return hashlib.sha256(conversation_id.encode()).digest()[:16].hex()
    return secrets.token_bytes(16).hex()


def _make_span_id() -> str:
    """16-char hex spanId (fresh random per call)."""
    return secrets.token_bytes(8).hex()


def build_trace_context_headers(
    conversation_id: str | None = None,
) -> dict[str, str]:
    """Build W3C traceparent + X-TaOS-Conversation-Id headers.

    Args:
        conversation_id: The stable conversation/thread id.  When provided
            the traceId is derived deterministically so memory spans nest
            under the same OTel trace as the agent's chat span.

    Returns:
        A dict with ``traceparent`` and, when conversation_id is set,
        ``X-TaOS-Conversation-Id``.  Always safe to pass as HTTP headers.
    """
    trace_id = _make_trace_id(conversation_id)
    span_id = _make_span_id()
    headers: dict[str, str] = {
        "traceparent": f"00-{trace_id}-{span_id}-01",
    }
    if conversation_id:
        headers["X-TaOS-Conversation-Id"] = conversation_id
    return headers
