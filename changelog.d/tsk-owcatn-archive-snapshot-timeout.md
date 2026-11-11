### Fixed

- Agent deletion on dir-backed incus pools no longer 500s due to snapshot timeout. Added `SNAPSHOT_TIMEOUT_S = 1800` constant, `_run` now kills child processes on timeout, and `archive_agent_fully` returns a clear 504 with actionable error message when snapshot creation exceeds the timeout.