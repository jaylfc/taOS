# Project Files API

<!-- Agent-facing REST API: upload, list, and fetch a project's Files. -->

Member agents read and write a project's Files through the HTTP API, keyed on the
project **slug**. Authenticate with `Authorization: Bearer <token>`. The granted scope
is bound to this project: a token for a different project returns 404.

## One-write principle

POST to `/upload`, then GET it back under the same path. There is no second publish step.

- `POST /api/projects/{slug}/files/upload?path=<subdir>` — multipart form field `file`.
  Returns `{name, path, stored_as, size, status}`; `stored_as` is the real path relative
  to the files root, so compare it with what you meant to write. `path` is the target
  DIRECTORY (never the file name) and may be sent as `?path=` or as a form field; sending
  both with different values is a 400. Optional form field `filename=<name>` sets the
  stored name (a bare name only: `/`, `\`, `.` or `..` is a 400). Needs `files_write`.
- `POST /api/projects/{slug}/mkdir` with JSON `{"path": "<subdir>"}` — create a folder.

## List and fetch

- `GET /api/projects/{slug}/files?path=<subdir>` — list entries (`files_read`).
- `GET /api/projects/{slug}/files/{path}` — stream one file as raw bytes (`files_read`).
- `GET /api/projects/{slug}/stats` — `{total_files, total_size}` (`files_read`).
- `GET /api/projects/{slug}/files/watch` — SSE stream that pushes directory listing on changes.

Write routes need `files_write`; read routes need `files_read`.
