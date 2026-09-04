### Added

- Agent scope requests are now readable, not only writable:
  `GET /api/agents/registry/{canonical_id}/scope-requests` lists an agent's
  scope requests (optionally filtered with `?status=pending|accepted|refused`)
  and `GET /api/agents/registry/{canonical_id}/scope-requests/{req_id}` reads
  one. Previously the only handle on a pending request was the `request_id`
  embedded in the notification payload, so dismissing the notification left the
  request alive in the store but addressable by nobody — a failed approval was
  indistinguishable from a successful one, and requests silently filling the
  pending cap could not be inspected.
- Both reads are authorized exactly like create: the agent's own registry
  bearer token, or the owning user / an admin. Every other caller — including a
  different agent's token and an authenticated non-owner — gets the same
  existence-hiding `404 {"detail": "agent not found or not active"}` the
  neighbouring create/approve/deny routes return, so nobody can enumerate
  another user's agents' requests or use the route as an existence oracle.
- Both reads return an explicit public projection of the stored row. The
  deciding owner/admin's user id (`decided_by`) is withheld — the agent's own
  token may read these routes, and no other agent-reachable route discloses its
  owner's internal id — while `status`, `decided_ts` and `granted_scopes` keep
  the decision fully observable. The list response is bounded at 200 rows:
  pending requests are taken oldest-first and ahead of everything else, so the
  stranded request these reads exist to recover is the last row a full page
  drops, and decided requests fill the remainder newest-first. Each group is a
  separate indexed query with its own `LIMIT`, so read cost is bounded by the
  page size rather than by how much decision history the agent has
  accumulated. The `?status` filter accepts any casing.
