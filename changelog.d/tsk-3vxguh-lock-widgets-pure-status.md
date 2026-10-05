### Fixed

- `/auth/lock-widgets` now returns an empty string for an agent's status when no live container is present, instead of falling back to the configured `status` value. This keeps the pre-auth console endpoint pure and prevents config-derived status leakage before sign-in.
- `GET /api/device/v1/state` `demo` flag now uses the existing `_demo_enabled` helper instead of reading the env flag directly, matching the rest of the lock-screen demo paths.
