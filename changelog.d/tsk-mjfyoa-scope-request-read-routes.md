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
