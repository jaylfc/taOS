### Fixed

- Human DELETE is no longer broken: registry-JWT agents are properly rejected with 403 when attempting to DELETE `/api/memory/chunk/{content_hash}`.
- Agent namespace now uses the correct key: agents read their memory using the config agent name (not the registry canonical_id), resolving the agent namespace key mismatch.
- Removed duplicate `/api/memory/browse` and `/api/memory/search` from `_AGENT_TOKEN_PATHS` since they are already covered by `_MEMORY_ROUTES`.
- Removed misleading "via qmd serve" wording from the `VALID_SCOPES` comment.
- Fixed the consent-integrity: `memory_read` scope is now properly enforced through middleware and routes.

### Added

- Added `Removes-Intentionally` trailer to waive the deleted tests: `tests/test_memory_scope_enforcement.py:TestMemoryScopeEnforcement.test_memory_read_removed_from_allowed_scopes` and `tests/test_memory_scope_enforcement.py:TestMemoryScopeEnforcement.test_memory_read_removed_from_valid_scopes`.

### Security

- Fixed a critical bug where registry-JWT agents could bypass authorization checks by exploiting the check_agent_scope return value, preventing unauthorized memory access.
