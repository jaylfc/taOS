### Fixed

- `/api/cluster/leases/release` no longer reads `cluster._leases` outside the lease lock; `release_lease_result` now returns the lease epoch atomically so the response reports the real epoch of the lease it released (empty for unknown or already-released leases).
