### Added
- `/api/a2a/gpu/claim`, `/api/a2a/gpu/renew`, and `/api/a2a/gpu/release` now include the lease `epoch` in their responses; renew and release pass the held epoch to `ClusterManager` so a replaced lease is refused rather than silently renewed or released.
