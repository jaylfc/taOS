### Fixed
- Update restart no longer triggers agent drain: the controller unit template no longer includes ExecStop, so in-app updates and manual restarts (systemctl restart, Restart button) do not call prepare-shutdown. Real reboots/shutdowns still drain agents exactly once via taos-pre-shutdown.service.
