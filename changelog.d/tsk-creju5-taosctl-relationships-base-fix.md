### Fixed

- Replace the module-level `_BASE` constant with literal path strings in
  `taosctl` relationships commands so the route-coverage static test can
  resolve the client paths.
