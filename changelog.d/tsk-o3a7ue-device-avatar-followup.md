### Fixed
- Read cached avatar bytes off the event loop via `asyncio.to_thread` so cache hits no longer block the async handler.
- Validate cache hits against the full 12-byte LVIMG header built with the same `struct.pack` layout as the conversion path, regenerating from source when the cached header does not match.
- Strip whitespace after removing the `W/` weak-validator prefix in `If-None-Match` parsing so inputs like `W/ "<etag>"` return 304 correctly.
