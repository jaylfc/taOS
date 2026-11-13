### Fixed

- `busctl()` now returns `(0, None)` for rc=0 with empty or whitespace-only stdout (e.g. successful set-property or no-value method calls like Peer.Ping), instead of incorrectly returning `(1, None)`.