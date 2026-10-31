### Fixed
- `GET /api/decisions` and `GET /api/notifications` now accept an agent's registry JWT instead of rejecting it with 401; the agent sees only the decisions it raised (`decisions_write`) and the notifications it posted (`notifications_write`), never the user's inbox or bell.
