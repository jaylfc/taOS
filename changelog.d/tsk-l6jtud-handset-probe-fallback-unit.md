### Fixed: a desk machine set up with the kiosk session is no longer treated as a phone
- `hardware._detect_device_class()` now returns "mobile" only when BOTH `taos-kiosk.service` AND `plasma-mobile.service` units exist (via `systemctl cat`).
- `routes.system._session_mode_state()` reports `available: true` only when BOTH units are present.
- The AVAILABILITY comment in `routes/system.py` is updated to reflect that a handset requires both the kiosk unit (taOS shell) and the plasma unit (Plasma Mobile fallback).