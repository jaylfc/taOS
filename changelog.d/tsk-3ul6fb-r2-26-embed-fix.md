### Fixed
- Knowledge ingest (R2-26) now chunks at sentence boundaries with ~200-char
  overlap instead of fixed 2 000-char slices, deletes an item's old QMD chunks
  before re-embedding so stale chunks cannot accumulate, and reports a `partial`
  status (instead of silently `ready`) when some chunks fail to embed.
