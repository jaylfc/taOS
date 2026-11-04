### Fixed

- `AgentRegistryStore.adopt()`: move the normalised active-handle holder check inside `_write_lock` so concurrent adopt() calls cannot both pass it; keep suffix shape, normalised handle guard, revoked_at stamp, and self reports_to validation intact.
