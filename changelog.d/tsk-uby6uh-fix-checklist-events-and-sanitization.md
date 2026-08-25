### Fixed

- Fixed checklist.event.created events being published under wrong project_id - now publishes under the task's project_id (mirroring task mutation behavior)
- Added None-safety check in archive_checklist_item() to raise clean "checklist item not found" error instead of TypeError
- Fixed update_checklist_item() return type annotation to return dict | None instead of dict
