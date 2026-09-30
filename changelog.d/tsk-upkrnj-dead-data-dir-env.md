### Fixed

- Per-test `data/` mutation guard in `tests/conftest.py` now uses `request.node.nodeid`, snapshots `(st_mtime_ns, st_size)`, and defers reports to session end under xdist so tests are not falsely blamed for writes by a parallel worker.
- Merged the two `pytest_sessionfinish` hooks into one so the litellm orphan-process leak guard is no longer shadowed. Added guard-uniqueness test `tests/test_conftest_hooks_unique.py`.
