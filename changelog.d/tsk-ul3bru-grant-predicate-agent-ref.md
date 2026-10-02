### Added
- Extracted `active_project_grants()` predicate usable outside HTTP requests to check an agent's active project-scoped grants by canonical_id.
- Added `AgentRef` dataclass and `resolve_agent_refs()` to bridge registry canonical_id and config hex id identities for dispatcher task assignment.