### Fixed

- `GET /api/cluster/workers` now returns a minimal projection (`name`, `status`, `tier_id`) for unauthenticated callers instead of the full worker inventory. Authenticated admins still receive the complete record including hardware, models, backends, LAN addresses, and auth state.
- session-authenticated callers on exempt paths now receive the full X-Taos-Version (credential presented)
