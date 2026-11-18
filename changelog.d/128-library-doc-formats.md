### Added

- **Library: DOCX/PPTX/XLSX/EPUB processors (#128 Phase 1)** — the ingest pipeline now extracts text from Word, PowerPoint, Excel, and EPUB files. DOCX/PPTX/XLSX use optional dependencies (`python-docx`, `python-pptx`, `openpyxl` via the `library` extra) with graceful degradation when absent; EPUB uses stdlib zipfile + XML. Kind detection covers the new extensions and MIME types.

### Fixed

- **Library document processors: review hardening (#128 Phase 1)** -- legacy
  `.doc` / `.ppt` / `.xls` uploads now fall back to `FileProcessor` instead of
  being routed to the OOXML processors, which cannot read them. EPUB
  extraction bounds decompression (entry count, per-entry and total byte caps)
  and rejects XML that declares an entity or a DTD internal subset, closing
  the zip-bomb / XXE hole on untrusted uploads. Books whose chapters carry a
  plain DOCTYPE (EPUB 2 / EPUB 3) extract normally. The DOCX/PPTX/XLSX
  processors check the same decompression envelope before parsing, parse off
  the event loop, walk grouped PPTX shapes, and record a rejection in the
  item meta instead of leaving the item "ready" with no text.
