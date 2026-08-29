### Fixed

- Checklist item creation now raises `ValueError` when task is not found, preventing events from being published under task_id instead of project_id
- Added `created_by` column to `task_checklist_items` table and included it in INSERT statements and event payloads
- Fixed `test_survives_agent_restart` to actually restart store and verify persistence across store instances
- Fixed `archive_checklist_item` to include `reported_by` in event payload and raise `ValueError` when task is missing