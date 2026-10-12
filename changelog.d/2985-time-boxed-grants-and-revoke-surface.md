### Added

- Agents app: each registry entry now shows the project-bound grants its agent
  holds, grouped by project, with a per-scope revoke button that calls
  `POST /api/projects/{project_id}/members/revoke-agent`. Admins get the revoke
  control; owners see the grant list read-only (the revoke endpoint is
  admin-gated).

### Fixed

- Time-boxed agent grants: the approve path now populates `expires_at`
  (`now + duration_secs`) on every grant it writes when the requester asked for
  a bounded grant. Unbounded requests (`duration_secs` absent or 0) leave
  `expires_at` NULL, so grants stay revocable-only until revoked. Previously no
  code path ever set `expires_at`, so the `_grant_unexpired` check was dead
  weight and a grant could only ever be revoked, never expire.
- `agent_scope_requests` gains a `duration_secs` column; existing installs are
  migrated by a guarded `_post_init` `ALTER TABLE ... ADD COLUMN`.
