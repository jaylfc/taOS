- Fix agent scope request bell being archived while pending.
  - Disable archiving of agent_scope_requests and auth_requests notifications
    during clear-all and archive-read operations in the notification store.
  - Individual dismiss of such notifications marks them read but keeps them
    unarchived until approved or denied.
  - Only the _retire_* functions on approve/deny now archive these notifications.