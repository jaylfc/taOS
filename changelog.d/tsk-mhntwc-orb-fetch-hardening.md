### Fixed
- orb catalog_fetch: requests now send `Accept-Encoding: identity` and reject servers that return a compressed response, file I/O is moved off the event loop via `asyncio.to_thread`, and cached catalog/sig reads are bounded to cap+1 bytes.
