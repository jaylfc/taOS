### Fixed

- `_pids_listening_on` now adds `-sTCP:LISTEN` to the `lsof` command, so proxy restart only kills actual listeners on the port, not client connections such as the incus forkproxy.
