### Fixed

- Update-check tests: give `_FakeProc` a `returncode` attribute (matching the real subprocess contract) and mock `check_preflight` so the tracked-branch update-check test reaches the git subprocess path instead of failing early.
