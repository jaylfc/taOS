### Fixed

- Validate skill_id against the seeded skills table when assigning skills to agents. Previously, arbitrary skill_ids were accepted and persisted, causing junk grants and breaking integrity. Now non-existent skill_ids are rejected with 404 and no row is written. A seeded skill_id is still accepted and grants the agent the skill.
