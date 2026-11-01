### Fixed

- Cluster: `POST /api/cluster/tasks/report` now fails with 503 when trace write is lost (strict mode), binds worker HMAC callers to their identity (403 on mismatch), and validates `tokens` mapping (max 16 keys, key len 1..64, value 0..1e9 int) with 422 on invalid input.
- Cluster: sealed-bucket late sidecar write failure now raises `TraceWriteError` in strict mode (was silently returning "recorded").
- Cluster: failed DB commit now rolls back before JSONL fallback, preventing duplicate rows on retry.
- Cluster: `tokens` values now use `StrictInt` (numeric strings like `"5"` rejected with 422).
- Cluster: `duration_s` rejects `inf`/`nan` with 422.
- Docs: `caller` and `model` marked optional in task-report payload documentation.