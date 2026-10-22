### Added

- `tinyagentos.routes.orb`: HTTP route for Orb status (GET `/api/orb/status`), admin-gated, returns attached USB Orbs with port, vid, pid, serial_number, description fields, 503 if TransportUnavailable.