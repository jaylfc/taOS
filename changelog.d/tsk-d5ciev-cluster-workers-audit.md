### Fixed

- Block path-traversal model IDs in the archive promotion engine so a crafted `model_id` cannot escape the active models root.
- Correct capability-map carry-forward so a worker re-registering with `ram_mb: 0` (or other falsy-but-valid values) updates the stored value instead of silently keeping stale data.
- Guard `gpu` and `npu` fields against non-dict values from older worker agents, matching the existing `cpu` string guard and preventing `AttributeError` crashes on legacy heartbeats.
