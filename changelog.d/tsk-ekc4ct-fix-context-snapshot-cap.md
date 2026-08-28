### Fixed

- `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` no longer collapses oversized snapshots to an empty `_truncated` marker: it now drops the largest fields first until the serialized form fits within 32768 bytes, preserving smaller fields and recording the dropped field names in the marker.
