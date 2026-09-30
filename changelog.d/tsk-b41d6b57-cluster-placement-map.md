### Added
- `GET /api/cluster/map` (admin-only, `tinyagentos/routes/cluster_map.py`) — the
  read-only capability map and live placement view for the Cluster app. It
  aggregates the cluster manager's existing state (`get_workers()` and the GPU
  arbiter's `get_leases()`) per request and adds nothing to the manager, so it
  cannot become a second source of truth: `nodes` lists every registered node
  (offline rows included) with health, tier, hardware, VRAM, backends,
  per-model placement rows and active leases; `placement` marks each model
  `loaded` or `installed` from the worker's own `backends[].available_models`,
  so installed-but-stopped capacity is visible; `capabilities` unions what the
  whole mesh can do, split into `active_nodes` / `installed_nodes` /
  `potential_nodes`. The desktop Cluster app gains a **Map** tab rendering both
  halves. Read-only slice: no move, relocation or auto-organise (#897).
