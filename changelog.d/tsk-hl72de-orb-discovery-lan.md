### Added

- LAN Orb discovery via mDNS in tinyagentos/orb/discovery.py:
  - ORB_SERVICE_TYPE constant for _taos-orb._tcp.local.
  - _parse_service_info() extracts name, addresses, port, board, app, version from ServiceInfo
  - browse_lan_orbs(timeout=2.0) browses mDNS for `timeout` seconds, then adds up to 1 second per discovered name for service lookups; async callers must use asyncio.to_thread, returns sorted list
  - Imports zeroconf lazily inside function (already a core dependency)
  - Deduplicates announced names, skips None service info, sorts results by name
  - Ensures Zeroconf.close() called in finally block