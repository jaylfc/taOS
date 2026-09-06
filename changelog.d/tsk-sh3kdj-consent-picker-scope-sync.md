### Fixed
- The consent picker now renders the project picker for all 9 server project scopes, not just the 3 previously listed, so approving requests for `files_read`, `files_write`, `project_lists`, `project_notes`, `project_tasks_create`, or `project_tasks_update` no longer 400s.
- When the requested project does not resolve, picking a visible project or creating a new one now clears the not-found flag and re-enables the Allow button; the New button is also no longer blocked while the not-found message is shown.
