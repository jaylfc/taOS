### Fixed

- Fixed `manifest.read_text()` OSError/UnicodeError in `lint_managed` and `lint_model_fields` to catch and report readable errors instead of tracebacks
- Changed missing-field warnings to print a one-line count plus first 10 lines, with full list available via `--verbose` flag
- Added repo root to `sys.path` before importing tinyagentos to prevent stale installed package