### Fixed

- Device event ids are seeded from a persisted high-water mark at startup, so a wall clock that moved backwards across a restart (Raspberry Pi without an RTC) can no longer hand out ids below ones the previous process already sent; a stale Last-Event-ID now always falls back to the snapshot path.