### Fixed

- POST `/api/projects/{project_id}/tasks/{task_id}/comments` now rejects an
  empty or whitespace-only `body` with 422 before storing, preventing
  board-noise comments from CLI misuse.
