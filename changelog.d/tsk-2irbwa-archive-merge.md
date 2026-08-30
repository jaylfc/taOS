### Fixed
- Notification archive tab now merges store mutations into its local state by id instead of replacing, so server-only archived rows fetched from `/api/notifications/archived` survive subsequent store changes.
- Archive tab local state is wiped when the store `clearAll` action fires, preventing stale items from reappearing after an explicit clear.
- Fixed a race in `fetchArchived` where an aborted in-flight request could clear the loading spinner while a newer request was still pending.
