### Added

- `GET /api/system/location` serves the handset's latest GPS fix from the runtime file `taos-locationd` writes (`/run/taos-location/location.json`). Signed-in users only, never logged; `available` is false on hosts without the location daemon, and a missing, stale (over 30 min), unreadable or malformed file reads as `fix: null`, never a server error.
