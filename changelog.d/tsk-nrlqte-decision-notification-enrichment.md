### Fixed
- Enrich the agent tool and app-permissions decision notification sites with `data` (kind, url, priority, from_agent, options), matching the existing REST route
- Cap notification option lists to the first 4 options with labels truncated to 40 characters
- Set `aps.thread-id` on APNs decision pushes so repeat notifications coalesce into a single thread
- Set `aps.interruption-level` to `time-sensitive` only for blocking-priority decisions
