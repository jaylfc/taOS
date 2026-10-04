### Changed

- `scripts/install-server.sh`: `ensure_container_runtime()` now auto-installs Incus from the official `pacman` and `apk` repos on Arch and Alpine, and starts the daemon under the running init system.
