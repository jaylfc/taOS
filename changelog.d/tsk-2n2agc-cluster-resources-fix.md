### Fixed

- Cluster resources backend IDs are now unique per (worker, backend) (`backend:<worker>:<name>`) to avoid collisions when multiple workers report the same backend name. App runtime lookups no longer drop subsequent installed apps if `get_runtime_location` raises for one app; that app is reported with state `unknown` and warning `runtime lookup failed`, and the error is not recorded in `errors` (only `list_installed()` failures are).
