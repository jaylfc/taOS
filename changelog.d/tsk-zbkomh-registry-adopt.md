### Added

- `AgentRegistryStore.adopt()` on `tinyagentos/agent_registry_store.py`: persists a canonical_id byte-for-byte on a joining device (account-device-sync S1) instead of minting a new one, with idempotent re-adopt, reserved-prefix guard, active-handle uniqueness, and a `allow_reserved` escape hatch for the internal taOS Agent path.