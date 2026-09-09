### Fixed

- Decision notifications from agent tool path (`tinyagentos/tools/decision_tools.py`) and app-permissions path (`tinyagentos/routes/app_permissions.py`) now enrich the notification payload with `data` carrying `decision_id`, `decision_type`, `url`, `kind`, `priority`, and `from_agent`, so they are actionable with category and buttons on iOS.
- Option lists in decision notifications are capped to the first 4 options with labels truncated to 40 characters to stay within the APNs 4KB payload limit.
- APNs payload now carries `aps["thread-id"]` set to the `decision_id` for coalescing repeat notifications, and `aps["interruption-level"]` set to `"time-sensitive"` only when `priority == "blocking"`.
- Per-type category taxonomy (`DECISION_APPROVE_DENY`, `DECISION_OPTIONS`, `DECISION_FREE_TEXT`) is preserved deliberately; a single flat `DECISION` category would collapse distinct button sets.