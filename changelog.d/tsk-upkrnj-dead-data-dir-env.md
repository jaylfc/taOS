### Fixed

- Per-test `data/` mutation guard in `tests/conftest.py` now uses `request.node.nodeid`, snapshots `(st_mtime_ns, st_size)`, and defers reports to session end under xdist so tests are not falsely blamed for writes by a parallel worker.
