### Added

- **Library: DOCX/PPTX/XLSX/EPUB processors (#128 Phase 1)** — the ingest pipeline now extracts text from Word, PowerPoint, Excel, and EPUB files. DOCX/PPTX/XLSX use optional dependencies (`python-docx`, `python-pptx`, `openpyxl` via the `library` extra) with graceful degradation when absent; EPUB uses stdlib zipfile + XML. Kind detection covers the new extensions and MIME types.

### Fixed

- **Library document processors: review hardening (#128 Phase 1)** -- legacy
  `.doc` / `.ppt` / `.xls` uploads now fall back to `FileProcessor` instead of
  being routed to the OOXML processors, which cannot read them. EPUB
  extraction bounds decompression (entry count, per-entry and total byte caps)
  and rejects XML that declares a DTD or an entity, closing the zip-bomb /
  XXE hole on untrusted uploads.
