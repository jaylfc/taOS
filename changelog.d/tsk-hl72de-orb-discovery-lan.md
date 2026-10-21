### Added

- LAN Orb discovery via mDNS in tinyagentos/orb/discovery.py:
  - ORB_SERVICE_TYPE constant for _taos-orb._tcp.local.
  - _parse_service_info() extracts name, addresses, port, board, app, version from ServiceInfo
  - browse_lan_orbs(timeout=2.0) browses mDNS, blocks for timeout seconds, returns sorted list
  - Imports zeroconf lazily inside function (already a core dependency)
  - Deduplicates announced names, skips None service info, sorts results by name
  - Ensures Zeroconf.close() called in finally block