### Fixed
- POST /api/memory/remember now rejects empty or whitespace-only `agent` values from admin/host callers with 400 "agent is required"