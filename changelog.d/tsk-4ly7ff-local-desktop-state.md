### Added

- Added `GET /api/system/local-desktop` endpoint that reports `available`, `enabled`, and `active` state of `taos-kiosk.service` via `systemctl cat`, `systemctl is-enabled`, and `systemctl is-active`.