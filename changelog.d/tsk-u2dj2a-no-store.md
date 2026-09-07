### Fixed

- `GET /api/providers`, `GET /api/secrets/{name}`, `GET /api/secrets/agent/{name}`, and `GET /api/secrets/agent/{name}/github` now carry `Cache-Control: no-store` and `Pragma: no-cache` so secret-bearing responses cannot be replayed from a shared cache or the browser back/forward cache.
