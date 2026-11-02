### Fixed
- orb catalog_fetch: requests now send `Accept-Encoding: identity` and reject servers that return a compressed response, file I/O is moved off the event loop via `asyncio.to_thread`, and cached catalog/sig reads are bounded to cap+1 bytes. The non-firmware kind refusal runs first inside the off-loop accept-and-cache step, before the serial is recorded or the cache written.
