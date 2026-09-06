### Fixed

- Lease resource allowlist now comes from the worker's scheduler resource inventory (`resources` field) instead of `backends[].name`. This fixes the issue where real claims were being rejected because resource IDs and backend names use different naming conventions.

- Added `resources` field to WorkerInfo and included it in:
  - Worker registration payload (`/api/cluster/workers`)
  - Worker heartbeat payload (`/api/cluster/heartbeat`)
  - SQLite persistence in ClusterManager
  - Controller memory tracking

- Added per-worker lease cap (`max_leases_per_worker: int = 10`) to ClusterManager constructor, counted only for ACTIVE (unexpired) leases per worker.

- Updated `_worker_for_resource()` validation logic to:
  - If worker has non-empty resources inventory, validate resource_id against it
  - If worker has no inventory (older worker), fall back to grammar validation regex
  - This maintains backward compatibility while fixing real claims

- All existing tests in `tests/test_leases.py` pass (37 passed, 0 failed)