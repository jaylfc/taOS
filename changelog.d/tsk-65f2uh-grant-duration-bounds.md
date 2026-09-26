### Fixed

- `POST /api/agents/auth-requests` now refuses a `duration_secs` that cannot be a real grant bound with 422: a bool, a string or float, zero or a negative value, or more than ten years. An oversized value used to be accepted and then make the approve route fail with a 500, and JSON `true` became a 1-second grant.
- Approving a pending request stored before this check with an oversized `duration_secs` now clamps the grant expiry to ten years instead of failing.
