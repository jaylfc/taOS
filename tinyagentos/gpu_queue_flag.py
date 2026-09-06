"""Rollout flag for the unified GPU work queue (taOS #1864).

off    - all queue behavior disabled; every path is pre-feature behavior.
shadow - instrument only: pull sites still 503 but record what would have
         queued; the gateway (Phase C) forwards without gating.
on     - full queue-and-wait behavior.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

GPU_QUEUE_MODES = ("off", "shadow", "on")
_warned = False


def gpu_queue_mode() -> str:
    global _warned
    raw = (os.environ.get("TAOS_GPU_QUEUE") or "off").strip().lower()
    if raw not in GPU_QUEUE_MODES:
        if not _warned:
            logger.warning(
                "TAOS_GPU_QUEUE=%r not in %s; using 'off'",
                raw,
                GPU_QUEUE_MODES,
            )
            _warned = True
        return "off"
    return raw
