### Fixed

- Library file typing now uses `mimetypes.guess_type()` to properly detect text files, including `.yaml`, `.yml`, and `.toml` extensions for text extraction (R2-27). .py files remain as `FileProcessor` and .log files use `TextProcessor` as required by the audit.

