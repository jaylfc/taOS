### Changed

- **Memory shelf grants and agent token renewal.** The grants feed now carries `memory:shelf` records (each with its own `shelf` field) that bind each agent to its own memory shelf,; an owner can opt in to reading an owned agent's shelf (`PUT/DELETE /api/agents/{name}/memory-read`). Agent registry tokens now expire after 30 days and renew through `POST /api/agents/registry/refresh-token`, which also accepts a token that lapsed up to 14 days ago, without retiring the token the agent is still running with.
