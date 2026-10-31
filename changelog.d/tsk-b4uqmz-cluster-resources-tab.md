### Added

- Cluster app: new read-only **Resources** tab listing every agent, backend, app and the controller from `GET /api/cluster/resources` in one table (name, type, worker, project, state, CPU, RAM, warnings). Filter by type, worker, project or state, or search by name. Sources the server could not read are shown as a notice above the table instead of an unexplained empty list.
