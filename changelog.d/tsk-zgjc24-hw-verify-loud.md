### Fixed
- install-server.sh now POSTs `/api/system/hardware/refresh` (the route is
  POST-only; a GET returned 405 and `curl -sf` swallowed it, producing the
  silent "hardware verification skipped" line from taOS #2 on the Orange Pi
  5B run). The verification step also retries for up to 30 s so a controller
  still finishing first-boot init is no longer treated as a failure, and on
  a persistent empty response it now dies loud with the journal tail and a
  checklist of what to inspect (`/dev/rknpu`, data dir writability, journal)
  instead of silently exiting 0. The detected profile (NPU type, device,
  profile_id) is now surfaced in the success banner so a tester can confirm
  at a glance that the NPU was recognised.
