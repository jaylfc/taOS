### Fixed

- `POST /api/memory/remember` human/admin path (`agent is None` block) now resolves the
  target config agent and authorizes ownership of the agent that RECEIVES the write, not
  the raw `body.agent`. Previously the owner check ran on the raw value (which may be a
  registry slug) and only afterwards did `find_agent` pick a config agent by name; a
  non-admin who owns a registry agent whose slug collides with another user's config agent
  name could write into that user's index. A config agent with no `registry_canonical_id`
  falls back to the raw value, which the registry cannot attribute, so non-admins get 403
  (fail closed) while admins pass. The bound-local-token and registry-JWT branches are
  unchanged by the hlr54s change (the registry-JWT branch was later changed by tsk-jke3kk).