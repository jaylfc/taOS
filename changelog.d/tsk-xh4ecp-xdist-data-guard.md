### Fixed

- xdist-aware `data/` mutation guard in `tests/conftest.py` now propagates worker-side mutations through `workeroutput` to the controller via `pytest_testnodedown`, so the run fails with rc=1 when any worker writes into `PROJECT_DIR/data`.
