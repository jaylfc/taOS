### Fixed

- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` size-budgets the `_truncated` marker so it cannot itself breach the 32768-byte cap, appends every later removal to the dropped count so the marker stays accurate, and never returns a snapshot over the limit.