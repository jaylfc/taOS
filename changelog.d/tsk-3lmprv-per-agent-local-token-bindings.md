### Fixed

- Per-agent local-token bindings store now serialises read-modify-write cycles
  under a process-shared file lock, preventing concurrent deploys from dropping
  one another's binding. Corrupt or mis-shaped bindings files cause
  `bind_local_token_agent` to raise rather than silently resetting the map to
  empty and overwriting it; `validate_local_token` and `get_local_token_agent`
  treat a non-dict bindings file as having no bindings instead of raising.
