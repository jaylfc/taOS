### Fixed
- `open_tar_gz` now translates `zlib.error` (corrupt deflate data) to `ArchiveError` in both except clauses
