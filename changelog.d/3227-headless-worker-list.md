### Added

- Cluster: headless callers get the full worker list. `GET /api/cluster/workers`
  is auth-exempt, but the exempt branch ignored a bearer token, so headless
  integrations (local token, device token, registry JWT) only saw
  `{name, status, tier_id, kind}`. On exempt `GET` paths the middleware now
  honours a valid bearer token: a valid local token authenticates as the primary
  user (admin only once a primary user exists, same as the non-exempt flow); a
  valid device token holding `agents:read`, or a registry JWT from an active
  agent, sets `headless_full_view`, and `GET /api/cluster/workers` serves those
  callers the full worker objects. A device token without `agents:read` stays
  anonymous. `signing_key` stays stripped for everyone and anonymous requests
  are unchanged. The promotion is skipped on routes that own their credential
  check (the LLM gateway), so a local token is still refused there (#3227).
