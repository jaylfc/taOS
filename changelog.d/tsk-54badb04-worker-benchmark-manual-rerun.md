### Added

- Cluster app: the worker detail panel now shows that worker's benchmark
  results (newest number per capability and model, how many runs are recorded
  and when the last one ran) with a "Re-run benchmarks" button. A queued run is
  handed to the worker on its next heartbeat -- nothing re-runs on its own, so
  the user stays in control of when a worker is occupied.
- `POST /api/workers/{id}/benchmark` (admin-only) queues a manual benchmark run
  for one worker: `409` while a run is already queued, `{"force": true}`
  replaces the queued run, and the queue entry is cleared once the worker posts
  its results. `GET /api/workers/{id}/benchmark` now also returns the queued run
  as `pending`.
