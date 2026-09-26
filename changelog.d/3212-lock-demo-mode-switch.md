### Added
- Settings -> Demo mode (admin): one switch that shows or hides the lock
  screen's scripted agents, notifications, panels and decisions. The
  `TAOS_LOCK_DEMO_*` flags still define what demo content exists; with the
  switch off every lock-screen surface behaves as if its flag were unset. It
  is stored in `data/demo_mode.json` and starts ON wherever a demo flag is set,
  so an existing demo device is unchanged until someone flips it.
