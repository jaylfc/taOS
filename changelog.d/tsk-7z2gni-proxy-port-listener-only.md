### Fixed
- `_pids_listening_on` now uses `lsof -ti -a -iTCP:{port} -sTCP:LISTEN` so
  client sockets on the same port are no longer returned. Proxy restart
  therefore no longer kills the incus forkproxy that holds a client
  connection. Spawn PID is also preferred over any port-scan result.
