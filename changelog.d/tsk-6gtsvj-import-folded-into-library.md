### Added
- Library: dropped or picked local files (.txt, .md, .pdf, .html, .json, .csv) become Library items through the ingest pipeline (POST /api/library/ingest); the processing queue refreshes after upload. The drop zone is a keyboard-accessible region with a Browse button and a progress indicator
- Library: "Embed files into an agent" button in the drop-zone card is a separate action: pick files and an agent, the files are staged via /api/import/upload and then embedded with /api/import/embed; success and errors are shown

### Removed
- Import app (standalone launchpad tile, upload icon) -- functionality folded into Library
- ImportApp.tsx deleted

### Changed
- Library is now the single universal ingestion surface for both URLs and local files
- Registry: removed the "import" app id only; other launchpad orders are unchanged
