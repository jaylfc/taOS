### Added

- Agents app: each registry entry now shows the project-bound grants its agent
  holds, grouped by project, with a per-scope revoke button that calls
  `POST /api/projects/{project_id}/members/revoke-agent`. This is the UI half of
  the scope-revoke surface; the endpoint itself already shipped (#3213).
  The panel renders for admins only, matching the admin-gated grant feed
  (`GET /api/agents/registry/grants`), so a non-admin owner is not shown a
  surface it cannot read.
