### Fixed
- test_constraints_file_matches_lock now uses `uv export --locked` and fails fast with a clear error when uv.lock is stale instead of silently rewriting it.