### Fixed

- Fixed file kind detection in `library_pipeline.py` so that `.yaml`, `.yml`, `.log`, `.toml`, `.py` and other text-based file extensions are properly detected as `"text"` kind instead of falling back to `FileProcessor`. The fix uses `mimetypes.guess_type` and adds explicit mapping for common text file extensions. This ensures `.yaml` and `.log` files get text extraction from TextProcessor instead of only metadata from FileProcessor.
