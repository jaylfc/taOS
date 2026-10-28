### Added
- TokenBucket and RateLimiter accept an injectable `clock` callable so tests can control time without monkeypatching the global `time.monotonic`.