from __future__ import annotations

import os
import time

from tinyagentos.projects.strike_store import StrikeStore


async def bounce_card(
    task_id: str,
    store,
    strikes: StrikeStore,
    log_tail: str = "",
    actor: str = "",
) -> None:
    """Record a failed dispatch attempt for *task_id*.

    Writes a timestamp line to ``/tmp/taos-attempts-<task_id>`` so the
    reaper and ``_burned()`` can count attempts from the local filesystem.
    Also records a strike through the durable board store so the lead can
    see the real ``strike_count`` on the task card.
    """
    attempts_file = f"/tmp/taos-attempts-{task_id}"
    with open(attempts_file, "a") as fh:
        fh.write(f"{time.time()}\n")

    try:
        await strikes.record_strike(
            task_id, "dispatch_failed", log_tail=log_tail, actor=actor
        )
    except Exception:
        pass
