### Fixed
- Move `avatar_hash` off the event loop onto a worker thread so 304s and cache hits no longer block the async loop on file reads.
- Parse `If-None-Match` per RFC 7232 so comma-separated, `*`, and weak (`W/`) validators all return 304 correctly.
- Validate cached LVIMG bytes before serving them, falling back to conversion when the cache is truncated or malformed so cache hits never 500.
