### Fixed
- Wake-budget enforcement no longer fails open on a damaged `wake_budget.json`: an absent file is treated as a fresh state, while a present-but-unreadable or unparseable file makes `can_wake` return False (fail closed) and surfaces the corruption in the log instead of silently restoring a full budget fleet-wide.
- `record_scheduled_wake` in the heartbeat loop is now called before the debounce stamp and outside the per-agent `try/except`, so a persistence failure propagates to the sweep-level handler rather than silencing the agent for the cooldown on an uncharged wake.
- `AppConfig.wake_budget` nested mappings (`per_agent`, `per_project`) are deep-copied per instance at both construction sites, preventing cross-instance and `DEFAULT_CONFIG` leakage.
