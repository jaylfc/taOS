"""Shared OTel trace-id derivation helper — Phase 2.

A single authoritative implementation so ``trace_context.py`` and
``emitter.py`` are guaranteed to produce identical traceIds for the same
conversation, preserving cross-module trace continuity.
"""
from __future__ import annotations

import hashlib
import secrets


def make_trace_id(conversation_id: str | None) -> str:
    """Return a 32-char hex traceId (128-bit).

    Deterministic when *conversation_id* is given: SHA-256 of the id,
    first 16 bytes.  Random otherwise (fresh ``secrets.token_bytes``).
    """
    if conversation_id:
        return hashlib.sha256(conversation_id.encode()).digest()[:16].hex()
    return secrets.token_bytes(16).hex()
