### Fixed

- Device TLS: reuse the stored cert only when its key matches. If a write is interrupted, the next load detects the mismatch and regenerates the pair.
