### Fixed

- `_run` in `update_runner.py` now accepts a `timeout` parameter and kills the child process on expiry, raising `asyncio.TimeoutError` instead of hanging indefinitely.
- `_run` raises `RuntimeError` on non-zero subprocess exit codes instead of silently returning the failure; callers in `switch_to_branch` catch the exception and return `ok=False` to preserve the existing graceful-error API.
- Removed dead `update_to_master` function (-132 LOC), which ran `git reset --hard` and was no longer imported by any production code.
