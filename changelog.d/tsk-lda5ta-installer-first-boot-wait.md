### Fixed

- Installer no longer gives up on the controller after 120 s on first boot. The wait is split into a 60 s port-open phase (using `/api/health`) and a 240 s readiness phase (using `/api/cluster/workers`), with an error message that names first-boot init when the port is open but the app is still starting. This prevents the false "install failed" report on slow first boots where a re-run would succeed (taOS#2).
