### Added

- `memory_read` scope for agents to access their own memory via `/api/memory/browse`, `/api/memory/search`, and `/api/memory/collections/{agent_name}`. Registry-JWT agents must now hold this scope to reach these routes, and results are restricted to the calling agent's own namespace. The DELETE `/api/memory/chunk/{content_hash}` route remains human-only, and agents cannot access another agent's memory even with `memory_read`.

### Fixed

- Memory routes now properly enforce authorization checks for registry-JWT agents, preventing unauthorized cross-agent memory access.
- The consent UI now displays the `memory_read` scope as an available grantable scope.
- Documentation updated to include the `memory_read` scope in the agent API surface documentation.