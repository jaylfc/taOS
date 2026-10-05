### Fixed

- `GET /api/device/v1/state` now shows unowned pending decisions (`user_id ""`) to devices paired to an admin owner, matching the Decisions app's behaviour. Non-admin owners still see only their own pending decisions.
