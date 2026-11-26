### Added

- Software updates: the controller can now check for updates. `GET /api/updates` reports the installed release, the last check, any newer release and the maintenance helper's status; `POST /api/updates/check` fetches the signed update manifest (primary feed, then mirror) and verifies its signature. Both routes are admin-only. `python -m tinyagentos.updates status|check` prints the same data as JSON.
