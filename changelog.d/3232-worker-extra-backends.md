### Added
- Worker: `TAOS_EXTRA_BACKENDS="type=url,..."` adds local backends (loopback or private-LAN literal IPs, probeable types only) to the probe list, and the worker manifest's `health_url`/`port` are now probed, so running manifest software reports live instead of `stopped` (#3232).
