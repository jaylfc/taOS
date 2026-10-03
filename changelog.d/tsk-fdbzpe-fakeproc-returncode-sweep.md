### Fixed

- Update-check tests: give `_FakeProc` a `returncode` attribute (matching the real subprocess contract) and mock `check_preflight` so the tracked-branch update-check test reaches the git subprocess path instead of failing early.
- Assert on `captured_proc.returncode == 0` so the attribute is load-bearing rather than merely present.
- Use `lambda *a, **k: []` for the `check_preflight` mock so it is signature-agnostic.
