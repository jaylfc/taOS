### Fixed
- POST /api/agents/{agent_id}/skills now validates skill_id against the seeded skills table and returns 404 instead of accepting arbitrary skill_ids.