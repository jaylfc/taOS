### Fixed

- An empty `Authorization` header on `POST /api/agents/auth-requests` now reports `proof_status="rejected"` (and `proven_canonical_id=None`) instead of incorrectly reporting `proof_status="none"`. Presence of the header is detected by `is not None`, not by truthiness.
