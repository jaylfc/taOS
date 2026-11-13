### Fixed

- Self secrets read (`GET /api/agents/self/secrets/{name}`) no longer falls back to
  the registry record's `display_name` when no config agent carries
  `registry_canonical_id` matching the token's `canonical_id`. A self-joined
  identity whose claim collides with a config agent's name can no longer read
  that agent's granted secrets; an unbound token now returns the same 404 body
  as an ungranted name, with no oracle on whether the caller is bound.

- Security: `get_self_secret` now raises 401 when `check_agent_scope` returns
  `None` (no agent token), and the agent-matching loop only matches when the
  config agent's `registry_canonical_id` is a non-empty string equal to `cid`,
  preventing `None == None` from silently selecting any unregistered agent's
  secrets.