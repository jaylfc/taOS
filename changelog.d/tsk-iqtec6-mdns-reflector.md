### Added

- Opt-in bundled mDNS reflector (`mdns_reflector.enabled` in config.yaml) that bridges cast discovery across a container bridge, with loop prevention (dedup by source and payload digest, never re-emits its own forwards) and a liveness self-check exposed at `GET /api/cast/discovery/status`, so a broken bridge reports `unavailable` instead of looking like an empty network.
