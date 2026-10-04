### Changed

- `scripts/install-server.sh`: `ensure_container_runtime()` now auto-installs Incus from the official `pacman` and `apk` repos on Arch and Alpine, and starts the daemon under the running init system.

### Fixed

- `scripts/install-server.sh`: `_start_incusd()` now detects the running init system via `/run` directories (systemd: `/run/systemd/system`, OpenRC: `/run/openrc`), returns failure when service commands fail, and skips `incus admin init` if the daemon cannot be started. The `apk` probe no longer uses a pipe that could SIGPIPE under `set -o pipefail`.
