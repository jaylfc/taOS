### Added
- Deterministic worker affinity routing via `affinity_key` on `TaskRouter.route_request` and `POST /api/cluster/route`. Workers are ordered by rendezvous hash first, then overflowed workers (load >= 0.9) are moved to the end so lightly loaded non-affinity workers are preferred.
- `TaskRouter.chat` now passes the requested model as `affinity_key` so the same model consistently routes to the same worker.
