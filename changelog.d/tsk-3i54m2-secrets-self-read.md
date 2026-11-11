### Fixed

- Self secrets read (`GET /api/agents/self/secrets/{name}`) no longer falls back to
  the registry record's `display_name` when no config agent carries
  `registry_canonical_id` matching the token's `canonical_id`. A self-joined
  identity whose claim collides with a config agent's name can no longer read
  that agent's granted secrets; an unbound token now returns the same 404 body
  as an ungranted name, with no oracle on whether the caller is bound.