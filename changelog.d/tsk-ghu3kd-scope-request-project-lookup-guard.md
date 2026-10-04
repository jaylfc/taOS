### Fixed
- Guard `project_store.get_project` in scope-request notification so a store error falls back to the raw project id instead of silently dropping the bell/toast notification.
