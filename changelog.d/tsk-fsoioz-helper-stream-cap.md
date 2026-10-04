### Fixed

- `check_tar_limits` and `extract_tar_safely` now default `max_total_stream_bytes` to the module-wide `MAX_TOTAL_STREAM_BYTES` cap, so helper-record bounding is active on every code path.
- `_wrap_tar_stream` now wraps `tar.fileobj` directly rather than the compression layer's inner stream, so the cap counts decompressed bytes consumed by the tar parser instead of compressed bytes on the wire.
- `_CountingStream` uses `tell()` to track position, preventing backward `seek()` from double-counting already-consumed bytes.
- The tar-walking loop is extracted into `_walk_tar` to remove duplication between the `stream=` and `tar=` branches.
