### Fixed
- Remove dead `version` and `requires_hardware` columns from `skills` table; switch `get_agent_skills` to an explicit column list so the schema and queries stay in sync.
- Route `AgentRegistryStore.revoke` through `set_status` so illegal lifecycle transitions raise `ValueError` consistently.
- Catch handle collisions in `AgentRegistryStore.update` and raise `ValueError` with the handle; the registry route now returns 409 instead of leaking a raw sqlite error.
- Add a test asserting the seeded builtin skill set and `SKILL_IMPLEMENTATIONS` never drift.
