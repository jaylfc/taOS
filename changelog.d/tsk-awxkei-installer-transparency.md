### Added
- `skip_models` flag on `POST /api/taosmd/setup` lets users defer memory-engine embedding model downloads to a later setup run. The setup wizard now labels downloads as "memory-engine embedding models (~N GB), required for memory search" so metered connections understand what is being fetched.
