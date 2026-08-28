### Fixed

- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` drops the largest fields first until the serialized form fits within 32768 bytes, preserving smaller fields and recording the dropped field names in a `_truncated` marker.