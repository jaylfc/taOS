### Fixed

- Project Files upload (`POST /api/projects/{slug}/files/upload`) now honours `path` sent as a multipart form field (previously silently discarded, so the file landed at the root), refuses a query-vs-form `path` mismatch with 400, accepts an optional `filename` form field to set the stored name (bare names only, anything with a path component is a 400), and echoes the real relative path back as `stored_as`. Query-string `?path=` callers are unchanged. (tsk-5qy5le)
