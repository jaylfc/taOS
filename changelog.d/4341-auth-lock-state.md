### Added

- `GET /auth/lock-state`: a loopback-only, cookie-free read that answers `{"locked": true|false}` for the handset's own screen, so on-device services can refuse to show or delete photos while it is locked.
- The controller now tracks the console lock state in memory: locked at start, unlocked only by a successful PIN, swipe or password sign-in from the local console, locked again by a console lock or logout.
- `GET /auth/lock-state` now answers `{"locked": bool, "session": int}`. `session` is a lock-session number: seeded from the clock at controller start, up by one each time the console goes from unlocked to locked, unchanged on unlock and on repeated lock signals while already locked, so one locked period carries one number.
