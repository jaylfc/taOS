### Fixed
- Drop write-never `version` and `requires_hardware` columns from the skills schema and replace `SELECT s.*` in `get_agent_skills` with an explicit column list
- Route `AgentRegistryStore.revoke` through the state-transition guard so illegal transitions raise `ValueError` consistently
- Catch `aiosqlite.IntegrityError` in `AgentRegistryStore.update` when a handle collides with another active agent and raise `ValueError` mentioning the handle, allowing the registry route to return 409
- Add a drift test asserting every seeded builtin skill id has a matching entry in `SKILL_IMPLEMENTATIONS` and vice versa
