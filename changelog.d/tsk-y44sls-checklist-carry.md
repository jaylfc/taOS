### Added
- OS-owned task checklist items: `GET`/`POST /api/projects/{project_id}/tasks/{task_id}/checklist-items` (list and create), with store-level create/update/archive in `task_store.py`, activity-feed events, and docs. POST requires the `project_tasks_create` grant; GET takes the default `project_tasks` grant; archive is store-level only and refuses unless the item is both verified and reported (#2674).

### Fixed
- Checklist item events (`checklist.item.created`, `checklist.item.archived`) are now published at the project scope so project subscribers receive them, instead of being keyed on the task id where no subscriber listened.
- `archive_checklist_item` on a nonexistent item now raises a clean `ValueError` instead of a `TypeError` from indexing `None`.
