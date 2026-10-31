### Added
- Worker yielded status support: workers can self-report status="yielded" to indicate they are occupied (e.g. gaming PC), and the controller routes around them, preserves status across status-less heartbeats, and resumes to online when the game ends.
