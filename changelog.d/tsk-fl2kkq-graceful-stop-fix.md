### Fixed

- Fixed graceful-stop script race condition where it reported readiness before the main controller process exited. The script now polls for process termination with a 30-second timeout before reporting success, ensuring the controller is actually down rather than just having a prepared API response.

- Updated taos-graceful-stop.sh to wait for controller process exit during `systemctl restart` operations, preventing the ~45-second systemd stop timeout when the process remained alive after the prepare-shutdown API call.