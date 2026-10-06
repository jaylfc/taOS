### Added

- Auth request `POST /api/agents/auth-requests` now returns a `proof_status` field with values `"none"` (no Authorization header), `"accepted"` (token validated, `proven_canonical_id` set), or `"rejected"` (header present, validation raised). This lets callers distinguish the three states that previously all returned the same status.