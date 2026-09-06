### Fixed

- Ensure temporary file cleanup always runs in `_atomic_write` function in `tinyagentos/routes/observatory.py`. Added `finally` block to delete temporary file even when `json.dumps()` raises an exception, preventing orphaned temporary files.