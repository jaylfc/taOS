### Fixed

- PATCH task status is now validated against the allowed enum (open, claimed, closed), returning 422 on a bogus value instead of silently dropping the write and vanishing the card; GET /api/projects/{pid}/tasks also rejects an invalid status query parameter with 422, and GET /api/projects/{pid}/tasks/{tid}/relationships validates direction up front (422) instead of raising ValueError and returning 500.
