### Fixed

- Cross-user event leakage via EventBus broadcast channel. Events with a `user:<id>` target are now routed only to that user's channel instead of being published to broadcast, preventing one authenticated user from seeing another user's events through `/api/events/stream` and `/api/os/events`. Notifications scoped to a specific user are also routed to the per-user channel; system-wide notifications (no `user_id`) continue to use broadcast.
