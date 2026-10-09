### Fixed

- Device event IDs now use a monotonic epoch (milliseconds since Unix epoch) as their starting point instead of 1. This ensures that after a controller restart, newly issued event IDs are always larger than any IDs from the previous process, preventing stale `Last-Event-ID` values from incorrectly matching the new ID range and causing missed state changes.