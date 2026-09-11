from __future__ import annotations

import os

from tinyagentos.projects.strike_store import StrikeStore


async def _burned(
    task_id: str,
    store,
    strikes: StrikeStore,
    burn_ttl: int = 3,
) -> bool:
    """Return True when *task_id* has exceeded the bounce threshold.

    The durable ``strike_count`` is consulted first; if the board store is
    unreachable the ``/tmp/taos-attempts-<task_id>`` file is used as a
    fallback.  A card is burned when either source reports ``>= burn_ttl``
    bounces.
    """
    try:
        durable_count = await strikes.count_strikes(task_id)
        if durable_count >= burn_ttl:
            return True
    except Exception:
        pass

    attempts_file = f"/tmp/taos-attempts-{task_id}"
    if not os.path.exists(attempts_file):
        return False

    with open(attempts_file) as fh:
        count = sum(1 for _ in fh)

    return count >= burn_ttl
