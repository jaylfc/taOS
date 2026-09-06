### Fixed

- Projects board: `PATCH /api/projects/{pid}/tasks/{tid}` no longer reports success for an edit it drops. Clearing a card's assignee, parent or element (sent as `null`, e.g. dragging a card to the board's "Unassigned" or "Orphans" lane) now persists, and a field the route cannot write — a `null` on a non-nullable field, a misspelled key, or a read-only column such as `id`/`created_by` — is rejected with 422 instead of answering 200 with the unchanged task.
