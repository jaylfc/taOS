### Fixed

- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` preserves `agent_id` and `session_id` by contract instead of by accident of value size: both drop loops now exclude them while any other field remains, so a resume note carrying a large `agent_id` beside many long-named fields no longer loses the identifiers the note exists to carry. They are still dropped if they alone breach the 32768-byte limit, which the capped snapshot never exceeds.
