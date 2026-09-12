### Fixed

- Projects router: Fixed half-finished store->pstore rename in six write handlers (update_project, archive_project, delete_project, add_member, set_project_lead, remove_member) that raised NameError at request time
- Projects events: Fixed ProjectEventBroker deadlock by releasing the lock before putting to subscriber queues and evicting oldest items on full queues instead of blocking
- Projects events: Preserved replay history on last-unsubscribe so reconnecting SSE clients can catch up on missed events
