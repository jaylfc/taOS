### Fixed

- **Memory write authorization**: `POST /api/memory/remember` now enforces agent ownership for human sessions (non-admin users can only write to agents they own; admins retain full access) and binds registry JWT callers by `registry_canonical_id` instead of display name, preventing a registry identity whose display_name matches a deployed agent's name from writing into that agent's namespace. For registry-JWT callers and non-admin human sessions, a config agent with no `registry_canonical_id` is refused (fail closed); admins may write to such agents.
