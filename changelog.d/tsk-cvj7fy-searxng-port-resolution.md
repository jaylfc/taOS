### Fixed
- `web_search` skill now resolves the SearXNG base URL from the installed-app runtime record instead of hardcoding `localhost:8888`. Supports `TAOS_SEARXNG_URL` env override, URL-encodes queries via httpx `params`, and surfaces `unresponsive_engines` in the error when SearXNG returns zero results.
