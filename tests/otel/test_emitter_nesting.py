"""RED-FIRST tests for OTel span nesting.

These tests assert that a child span's parentSpanId matches its parent's spanId,
and that a multi-level chain produces nested (not flat) spans.
"""
from __future__ import annotations

import hashlib
import time

from tinyagentos.otel.emitter import _build_otlp_span


def _sha256_span_id(env_id: str) -> str:
    return hashlib.sha256(env_id.encode()).digest()[:8].hex()


def _make_envelope(id_str: str, parent_id: str | None, kind: str = "llm_call") -> dict:
    return {
        "v": 1,
        "id": id_str,
        "trace_id": "trace-nest",
        "parent_id": parent_id,
        "created_at": time.time(),
        "agent_name": "nest-agent",
        "kind": kind,
        "channel_id": None,
        "thread_id": "conv-nest",
        "backend_name": "test-provider",
        "model": "test-model",
        "duration_ms": 10,
        "tokens_in": 1,
        "tokens_out": 1,
        "cost_usd": None,
        "error": None,
        "payload": {"status": "success", "messages": [], "response": "ok", "metadata": {}},
    }


def test_child_parent_span_id_matches_parent_span_id():
    parent_env = _make_envelope("env-parent", parent_id=None)
    child_env = _make_envelope("env-child", parent_id="env-parent")

    parent_span = _build_otlp_span(parent_env)
    child_span = _build_otlp_span(child_env)

    assert parent_span is not None
    assert child_span is not None
    assert child_span.get("parentSpanId") == parent_span["spanId"]


def test_three_level_chain_nests():
    parent_env = _make_envelope("env-grandparent", parent_id=None)
    child_env = _make_envelope("env-parent", parent_id="env-grandparent")
    grandchild_env = _make_envelope("env-child", parent_id="env-parent")

    parent_span = _build_otlp_span(parent_env)
    child_span = _build_otlp_span(child_env)
    grandchild_span = _build_otlp_span(grandchild_env)

    assert parent_span is not None
    assert child_span is not None
    assert grandchild_span is not None

    spans = [parent_span, child_span, grandchild_span]
    roots = [s for s in spans if "parentSpanId" not in s]
    assert len(roots) == 1, f"3 spans emitted, {len(roots)} roots found, expected 1"

    assert child_span["parentSpanId"] == parent_span["spanId"]
    assert grandchild_span["parentSpanId"] == child_span["spanId"]
