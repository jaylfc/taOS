### Fixed
- Archive tab no longer wipes active notifications from the shared store; archive rows are held in local component state and merged with existing store entries.
- Active notifications are no longer capped at 10 in the Notifications app; all active items are reachable.
- Dock pin redirect for `notification-archive` no longer carries an unread `section` field.
