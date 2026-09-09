### Added
- `skip_models` flag on `POST /api/taosmd/setup` lets users defer memory-engine embedding model downloads to a later setup run. The setup wizard now labels downloads as "memory-engine embedding models (~N GB), required for memory search" so metered connections understand what is being fetched.
- Deploying an agent with taOSmd memory after a deferred setup now self-heals: if `taosmd_default.json` has `models_skipped=true`, the deploy endpoint starts the deferred model pull before the agent is marked ready, returning 409 if the registry is unavailable.
