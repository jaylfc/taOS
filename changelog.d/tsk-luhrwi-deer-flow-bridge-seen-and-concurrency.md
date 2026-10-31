### Fixed

- DeerFlow bridge: mark `user_message` seen only after the reply POST succeeds, so a cooldown-suppressed message or a failed POST can be redelivered (mirrors the nanobot bridge fix).

- DeerFlow bridge: bound concurrent `user_message` tasks with `_MAX_INFLIGHT = 4` and a `_SEMAPHORE`, and drain pending tasks on stop (mirrors the nanobot bridge fix).