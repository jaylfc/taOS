### Fixed

- `find_model_hosts` now uses `placement.eligible` to filter workers (accepting `online` and `update-available`, excluding `draining` and `kind=device`) and picks `canonical_host` via rendezvous hash keyed on model id instead of alphabetical.
