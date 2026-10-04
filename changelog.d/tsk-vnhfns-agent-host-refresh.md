### Fixed
- Agent registry now refreshes stale container IPs from Incus at controller startup and on connect failure, so agents that moved bridges or projects keep working.
- Host refresh now persists the rewritten IP to config.yaml so the log line fires once instead of repeating on every boot.
