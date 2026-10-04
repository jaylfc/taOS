### Fixed

- The agent workspace upload endpoint (`POST /api/agents/{agent_name}/workspace/files/upload`) now honours `path` sent as a multipart form field (previously silently discarded, so the file landed at the root) and refuses a query-vs-form `path` mismatch with 400. Query-string `?path=` callers are unchanged.
