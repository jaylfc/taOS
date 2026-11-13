### Fixed

- `POST /api/memory/remember` human/admin path: when `body.agent` does not resolve to any config agent (neither by name nor by `registry_canonical_id`), the endpoint now returns `404 {"detail": "unknown agent"}` before any write attempt.
- `POST /api/memory/remember` 502 error responses no longer leak absolute server paths. Both the `_agent_db_path` OSError and the qmd `/ingest` failure now return `{"stored": false, "error": "memory store unavailable"}` while keeping the detailed warning in the log.
- `POST /api/memory/remember` qmd ingest path now includes a short UUID suffix (`remember/{timestamp}-{uuid4().hex[:8]}.md`) to prevent collisions when two writes occur in the same microsecond.