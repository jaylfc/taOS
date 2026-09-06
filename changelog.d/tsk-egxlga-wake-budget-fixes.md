### Fixed

- Wake-budget reporting now passes the agent's bound `project_id` instead of `None`, so per-project consumption is measured against the same state key that enforcement writes.
- Observatory `/api/observatory/wake-budget` now returns rows for agents with `status: "running"` (the only status the heartbeat wakes), instead of silently returning an empty list.
- Removed the unused `mention_cap` / `mention_count` half of the wake-budget module, which had zero production callers and structurally always reported zero mentions.
