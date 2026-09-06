### Fixed
- Checklist item create and archive now refuse a task that no longer exists
  instead of publishing their broker event under a topic no project subscriber
  listens to. `task_checklist_items.task_id` declares a foreign key but the
  task store never enables `PRAGMA foreign_keys = ON`, so a checklist item can
  outlive its task; the previous fallback resolved the publish topic to an
  empty string and the `checklist.item.created` / `checklist.item.archived`
  event was silently lost. Both paths now resolve the parent task before they
  mutate, so a refusal leaves no orphan row and no half-applied archive.
