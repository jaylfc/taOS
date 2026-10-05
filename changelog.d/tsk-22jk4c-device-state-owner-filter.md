### Fixed

- `GET /api/device/v1/state` now filters pending decisions by device owner, preventing a device paired to one owner from seeing another owner's pending decisions through a shared (no `user_id`) agent.
