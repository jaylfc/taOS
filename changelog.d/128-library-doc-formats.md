### Added

- **Library: DOCX/PPTX/XLSX/EPUB processors (#128 Phase 1)** — the ingest pipeline now extracts text from Word, PowerPoint, Excel, and EPUB files. DOCX/PPTX/XLSX use optional dependencies (`python-docx`, `python-pptx`, `openpyxl` via the `library` extra) with graceful degradation when absent; EPUB uses stdlib zipfile + XML. Kind detection covers the new extensions and MIME types.
