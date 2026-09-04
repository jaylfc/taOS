### Fixed

- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` now accounts for the `_truncated` and `_dropped` marker overhead inside the capping loop and records `_dropped` as a bounded count rather than an unbounded list, so a snapshot of many fields with small values can no longer return over the 32768-byte limit with zero real fields preserved.
