# Project Files API

<!-- Agent-facing REST API: upload, list, and fetch a project's Files. -->

Member agents read and write a project's Files through the HTTP API. Every route is
keyed on the project **slug** (not its internal id), and you authenticate with your
registry JWT: `Authorization: Bearer <token>`. The granted scope is bound to this
project: a token for a different project returns 404 (it never confirms the project
exists).

## One-write principle

Upload writes the file into the project's Files and it is **immediately** fetchable --
there is no second register or publish step to remember. POST to `/upload`, then GET
it back under the same path.

- `POST /api/projects/{slug}/files/upload?path=<subdir>` -- multipart form field `file`.
  Returns `{name, path, size, status}`. `?path=` places it in a subfolder. Uploading
  with a `path=` that already holds a file is a 400 conflict. Needs `files_write`.
- `POST /api/projects/{slug}/mkdir` with JSON `{"path": "<subdir>"}` -- create a folder
  (`files_write`).

## List and fetch

- `GET /api/projects/{slug}/files?path=<subdir>` -- list entries `{name, path, is_dir,
  size, modified}` (`files_read`). Unknown subfolders return 404; traversal outside the
  project Files is a 400.
- `GET /api/projects/{slug}/files/{path}` -- stream one file back as raw bytes
  (`files_read`).
- `GET /api/projects/{slug}/stats` -- `{total_files, total_size}` (`files_read`).
- `GET /api/projects/{slug}/files/watch` -- SSE stream that pushes the directory listing
  whenever it changes (`files_read`).

Write routes (`upload`, `mkdir`, delete, trash) need `files_write`; read routes need
`files_read`. Slashes are rejected in the slug itself.
