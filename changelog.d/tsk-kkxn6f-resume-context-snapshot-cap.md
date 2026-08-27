### Fixed

- Resume notes no longer carry an unbounded `context_snapshot` to `POST /resume`: `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` truncates it to a 32768-byte suffix with a `_truncated` marker, so an oversized transcript no longer saturates the woken agent's input window and restarts into the same overflow.
