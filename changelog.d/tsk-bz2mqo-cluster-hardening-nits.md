### Fixed
- Cluster manager hardening: `_format_hw` coerces non-integer `ram_mb`/`vram_mb`
  from worker heartbeats instead of raising `TypeError` (500); the register and
  heartbeat routes now reject non-integer hardware fields with `400`.
- Rejected stale-generation (or fenced) worker registrations no longer poison
  `_ever_seen`, so a subsequent valid registration correctly emits the
  `worker.join` notification.
- The worker storage-backup notification path in `routes/cluster.py` now reads
  `app.state.notifications` (the attribute that is actually assigned) instead of
  the never-set `app.state.notif_store`, so the notification fires.
- `ClusterManager.stop()` now drains `_background_tasks` with a 10-second
  timeout so fire-and-forget work completes before shutdown.
