### Fixed

- Root the taosmd agent registry at each app's resolved data_dir so in-app
  consumers (the /api/agents/deploy route, the v2 persona startup migration)
  use an app-scoped registry instead of the process-global default.
