### Fixed

- `check_tar_limits` now bounds PAX and GNU long-name/long-link helper record payloads through a stream cap, preventing an oversized helper from forcing full decompression before the per-member and cumulative size limits are evaluated.
