### Added

- OS-owned task checklist items for project tasks. `POST/GET /api/projects/{project_id}/tasks/{task_id}/checklist-items` create and list per-task checklist items (create takes a JSON `{"text"}`, list honours `?include_archived=`); items carry `done`/`verified`/`reported`/`archived` state and are archived only when verified+reported, publishing `checklist.item.created`/`checklist.item.archived` under the task's resolved `project_id` (where project subscribers are scoped) so a missing item raises `ValueError` rather than a `TypeError`.
