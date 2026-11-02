### Fixed

- DeerFlow bridge: mark `user_message` seen only after the reply POST succeeds, so a cooldown-suppressed message or a failed POST can be redelivered (mirrors the nanobot bridge fix).

- DeerFlow bridge: bound concurrent `user_message` tasks with `_MAX_INFLIGHT = 4` and a `_SEMAPHORE`, and drain pending tasks on stop (mirrors the nanobot bridge fix).
- DeerFlow bridge/adapter: remove `RemoteProtocolError` from retry set (prevents duplicate runs), treat 3xx as undelivered in `post_reply`, guard non-dict responses and non-dict `output`/`values`, add bounded shutdown drain with cancellation and `TimeoutStopSec=75` on bridge unit.
