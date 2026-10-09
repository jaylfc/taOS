### Fixed

- Joining the taOSgo mesh no longer takes over a host's existing tailscaled. When the default daemon is logged in to another control server (or its prefs cannot be read), the mesh join and leave refuse with `foreign-control-server` / `control-server-unknown` instead of re-pointing it or logging the host out of its own tailnet, and mesh status no longer reports that tailnet as the mesh. Set `TAOS_TAILSCALE_SOCKET` to drive a dedicated tailscaled for the mesh instead.
