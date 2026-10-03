### Fixed

- Retry-window resume now clears the `paused_by_restart` marker alongside
  `paused` in `_unpause`, so a successful retry leaves both flags `False` in
  the saved config.
- Non-restart pause setters (Agents app pause route, disk quota monitor,
  failure handler) now set `paused_by_restart = False` in the same write, so a
  stale marker can never turn a user, quota, or failure pause into a restart
  pause.
- Retry-window-expiry notification now distinguishes between successful config
  persistence ("paused flags have been cleared") and write failure ("config
  write failed; paused flags cleared in memory but may reappear after the next
  restart").