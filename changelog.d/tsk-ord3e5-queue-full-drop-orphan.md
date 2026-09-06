### Fixed
- `GpuArbiter._drain_queue` now removes the dropped task from `_queued_entries` when the queue-full drop branch fires, preventing phantom task_ids from persisting in `queue_snapshot()` and `queue_position()` forever (#tsk-ord3e5).
