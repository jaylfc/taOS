### Fixed

- Use `readability-lxml` for HTML extraction in both `knowledge_ingest.py` and `library_pipeline.py`
- Extract text properly handles HTML entities with `html.unescape()`
- Remove the 100-character threshold for readability extraction
- Fix regex pattern that broke when `>` appeared inside HTML attributes
- Maintain fallback to simple tag-stripping when `readability-lxml` is not installed