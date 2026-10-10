### Added

- Store verification check runner module `tinyagentos/store_verification.py` with async `run_checks()` function that performs manifest validation, light static build check, permissions scan, and port hygiene against an app package directory. Associated test file `tests/test_store_verification.py`.

### Fixed

- Test module collection error: added missing `from pathlib import Path` import to `tests/test_store_verification.py`.
- `runner_error` path now properly exercised by test with monkeypatch forcing an exception in `_check_manifest_valid`.
- Moved `parse_manifest`, `PackageError`, `_ALLOWED_TYPES` imports to module level in `tinyagentos/store_verification.py`; `_is_userspace_app_type` now uses shared `_ALLOWED_TYPES`.
- Removed dead code: `_parse_manifest_raw` helper and `_make_manifest_json` test helper.
- `_check_builds` now accepts `package.json` with `scripts.build` key as a static pass condition.
- `_check_builds` now validates that resolved `entry` path does not escape the package directory, failing with "entry escapes package dir" if it does.