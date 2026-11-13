### Fixed

- Fixed `manifest.read_text()` OSError/UnicodeError in `lint_managed` and `lint_model_fields` to catch and report readable errors instead of tracebacks
- Restructured warning print in `main()` so each warning prints exactly once: count line first, then first 10 without `--verbose`, or all warnings with `--verbose`
