### Added

- Store verification check runner module `tinyagentos/store_verification.py` with async `run_checks()` function that performs manifest validation, light static build check, permissions scan, and port hygiene against an app package directory. Associated test file `tests/test_store_verification.py`.