### Fixed
- Notification caps (title <= 120, message <= 1000, data <= 4 KB) no longer apply to the admin/human path. They are enforced only on the agent path (after agent_cid is resolved, before the rate-limit charge), preserving the documented behavior that the admin path is "unchanged".
