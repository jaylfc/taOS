### Fixed
- Deploy-time self-heal for deferred taOSmd models no longer times out after a fixed 300 s wall-clock cap; wait now polls until the pull task reaches a terminal state and only fails if progress/message stalls for 10 minutes. Concurrent deploys against the same deferred default attach to a single in-flight pull instead of starting duplicate downloads.
