### Fixed

- Declared `pypdf>=6.17` as a core dependency so PDF text extraction is actually available in every install, fixing empty PDF indexes.
- `PdfProcessor` now lets extraction errors propagate instead of swallowing them, so failed PDF items are marked `error` rather than silently `ready`.
