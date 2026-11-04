### Added

- `AgentRegistryStore.adopt()` on `tinyagentos/agent_registry_store.py`: persists a canonical_id byte-for-byte on a joining device (account-device-sync S1) instead of minting a new one, with idempotent re-adopt, reserved-prefix guard, active-handle uniqueness, and a `allow_reserved` escape hatch for the internal taOS Agent path.

### Fixed

- `AgentRegistryStore.adopt()`: accept collision suffixes of two or more hex digits (register() has no upper bound), guard the active-holder check against normalised handles so @taOSmd-dev and taosmd-dev collide, stamp revoked_at when status="revoked" so list_revoked() sees it, and reject reports_to equal to the adopting canonical_id ("an agent cannot report to itself").
- `AgentRegistryStore.adopt()`: run the normalised active-handle holder check before the write lock instead of inside async with self._write_lock:, per review feedback on tsk-lkgxq2.
