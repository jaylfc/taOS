### Fixed

- TextProcessor now streams file reads instead of loading the entire file into a single `str`, eliminating the 100 MB `str` copy on large text files.
- Article title extraction in `knowledge_ingest._download_article` now unescapes HTML entities (e.g. `&amp;` becomes `&`), matching the behaviour already present in `library_pipeline.WebProcessor`.
- ImageProcessor JPEG thumbnail conversion now handles `LA`, `PA`, `I;16` and other non-RGB/L/CMYK Pillow modes instead of raising on them.
- `x.py` `create_watch` now chains the `sqlite3.IntegrityError` via `raise ... from e`.
- `verify_registry_token` now raises `ValueError` with a clear message when the JWT payload is not a JSON object (dict).
