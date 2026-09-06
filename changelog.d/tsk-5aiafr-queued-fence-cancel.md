### Added
- `GpuArbiter.queue_snapshot()` now includes `resource_id` per entry, and a new `cancel_queued_for_resource(resource_id)` cancels every queued GPU op targeting a fenced resource through the same race-safe `_cancelled_ids` path as `cancel_op`. Fence handlers can now proactively cancel queued ops instead of waiting for `claim_lease` to reject them on admission (#tsk-5aiafr).
