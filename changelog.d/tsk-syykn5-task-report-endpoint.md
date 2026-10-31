### Fixed

- Cluster: `POST /api/cluster/tasks/report` now fails with 503 when trace write is lost (strict mode), binds worker HMAC callers to their identity (403 on mismatch), and validates `tokens` mapping (max 16 keys, key len 1..64, value 0..1e9 int) with 422 on invalid input.