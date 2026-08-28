### Fixed

- Resume notes no longer lose their entire `context_snapshot` when it exceeds the limit: `_cap_context_snapshot()` in `tinyagentos/restart_orchestrator.py` now drops the largest fields until the serialized form fits within 32768 bytes, preserving valid JSON and smaller keys such as `agent_id`, and records the dropped fields in a `_dropped` marker alongside `_truncated`.
