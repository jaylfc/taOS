### Fixed
- tsk-nu5wtc: dispatcher_tick_loop passes fresh now per user and records completion time (not tick start) to avoid stale lease expiry and premature poll skip.
