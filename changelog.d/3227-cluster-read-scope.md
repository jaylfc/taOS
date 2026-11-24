### Added

- Cluster: `cluster_read` grant scope for the worker list (taOS #3227). A
  registry JWT from an active agent holding an unexpired `cluster_read` grant
  now gets a full-but-not-admin view of `GET /api/cluster/workers`:
  capabilities, backends (including loaded models), resources and
  `free_vram_mb`, but no pairing state (`revoked`/`blocked`/`live_token`),
  `host_lan_ip` or `hardware`. `cluster_read` alone never promotes the caller
  to admin or to the unrestricted headless full view, and a JWT without a
  qualifying grant still falls through to the anonymous view. `signing_key`
  stays stripped for everyone.
