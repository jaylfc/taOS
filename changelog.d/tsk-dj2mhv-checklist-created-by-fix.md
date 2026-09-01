### Fixed
- Restored two deleted regression tests for checklist item event delivery and agent restart survival
- Fixed blind `ALTER TABLE` migration for `created_by` column: now checks `PRAGMA table_info` first and surfaces non-duplicate ALTER failures instead of swallowing them
- Added round-trip and existing-DB upgrade test coverage for `created_by` persistence on checklist items
