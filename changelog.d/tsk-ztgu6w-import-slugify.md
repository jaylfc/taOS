### Fixed

- The JSON agent-import endpoint (`POST /api/agents/import`) no longer persists a caller-supplied dict verbatim. Operational/privileged keys (`llm_key`, `permitted_models`, `registry_canonical_id`, `can_read_user_memory`) are stripped via a Pydantic allowlist model, and the agent name is now slugified with the same rule the create route uses, so a bundle naming an agent "My Agent" lands under container-safe slug `my-agent` instead of an un-routable key.
