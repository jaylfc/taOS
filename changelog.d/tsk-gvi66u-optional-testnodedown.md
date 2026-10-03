### Fixed

- `pytest_testnodedown` in `tests/conftest.py` is now marked `optionalhook=True`, so serial runs with xdist disabled (`-p no:xdist`) no longer INTERNALERROR on every test file.
