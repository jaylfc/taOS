### Security

- API responses under `/api/` and `/agent/` now carry `Cache-Control: no-store` unless the handler set its own policy, so per-user JSON (account data, secrets metadata, grants, project files) can no longer be held by a shared proxy cache or replayed from the browser's back/forward cache. SSE streams keep their `no-cache` and static assets keep their long cache.
