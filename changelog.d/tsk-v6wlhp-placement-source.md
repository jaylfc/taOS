### Added

- Added a pure `placement_source` helper function to `tinyagentos/cluster/placement.py` that returns the agent's `placement_source` value when present and truthy, falls back to `"user"` for legacy agents with a truthy `remote` field, and returns `None` otherwise.