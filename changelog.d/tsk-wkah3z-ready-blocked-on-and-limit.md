### Fixed
- `/api/projects/{pid}/tasks/ready` now honours `blocked-on:<id>` labels alongside `task_relationships` edges: a task carrying a `blocked-on:<id>` label whose target is OPEN in the same project is excluded from the ready set; closing the target re-surfaces it
- The route's `?limit` query param is now honoured: clamped to `[1, 500]`, so `?limit=0` and `?limit=-1` no longer silently widen to unbounded or fall back to the default 50
- `ready_tasks` (and its migration twin) now scope `blocked-on:<id>` label joins to the same project as the labelled task, so a cross-project `blocked-on:<id>` label can no longer hide a task in the wrong project
