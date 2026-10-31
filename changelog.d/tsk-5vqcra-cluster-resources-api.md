### Added

- `GET /api/cluster/resources` (admin-only, `tinyagentos/routes/cluster_resources.py`) -- one flat aggregate inventory of every resource taOS runs across the mesh (agents, backends, apps, controller), so the Cluster Resources view and a later Move action share a single source of truth. Read-only: aggregates per request from existing getters, every source wrapped so one failure (e.g. incus down) records its name in `errors` instead of 500-ing the response.
