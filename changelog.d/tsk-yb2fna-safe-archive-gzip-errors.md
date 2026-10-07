### Fixed

- A corrupt or truncated gzip tarball now raises `ArchiveError` when the damage is hit while members are being read, not only when `tarfile.open` fails, so callers see one error type for an invalid archive.
- `open_tar_gz` now translates `zlib.error` (corrupt deflate data) to `ArchiveError` in both except clauses.
