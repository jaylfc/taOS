### Fixed

- **Non-admin owners see their own agents' pending invites in the Agents app (#2067 follow-up)** — the pending-invites section used the admin-only cross-project aggregate, so a project owner who is not an admin saw no invites for agents they own. A non-admin owner now reads their own agents' pending invites from the new per-owner endpoint GET /api/agents/invites/pending/mine, with the same exclusions as the aggregate: pin hashes and expired invites are never returned, collab invites excluded. Admins keep the aggregate.
