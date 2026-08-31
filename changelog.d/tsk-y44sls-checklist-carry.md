### Fixed
- Checklist item events (`checklist.item.created`, `checklist.item.archived`) are now published at the project scope so project subscribers receive them, instead of being keyed on the task id where no subscriber listened.
- `archive_checklist_item` on a nonexistent item now raises a clean `ValueError` instead of a `TypeError` from indexing `None`.
