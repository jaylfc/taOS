### Added

- Added tripwire tests to verify `_HelperSizeGuardTarInfo._proc_member` hook is reached.
  - `test_helper_guard_hook_exists_on_this_python`: asserts the CPython private hook exists and hasn't been renamed.
  - `test_helper_guard_override_is_called`: verifies the guard is actually called during archive processing.
  - Updated four existing test assertions to use "tar helper record" error match instead of "exceeds" where the tests are proving the declared-size guard via `_HelperSizeGuardTarInfo._proc_member` (not `_ReadSizeGuard.read`). This ensures the CI fails if Python renames `_proc_member` and the guard silently becomes inactive.

These tests will fail if a future Python version renames `tarfile.TarInfo._proc_member`, protecting against silent fail-open of the size guard.