### Fixed
- Per-person broker preferences: the Keenable public-fallback lookup now resolves the signed-in person (session user_id), then the agent's owner for agent-bound tokens, then the shipped system default (primary owner), instead of always falling back to the legacy "user" scope which the migration empties.
