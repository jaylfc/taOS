### Fixed

- Serialize PIN login attempts per limiter key so concurrent requests cannot bypass the lockout threshold, and verify PIN hashes outside the global account lock.
