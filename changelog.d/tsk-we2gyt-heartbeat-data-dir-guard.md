### Fixed

- The agent heartbeat no longer silently swallows a missing `data_dir`: the wake-budget guard now lives at tick entry, so a missing `data_dir` fails loudly at the tick/sweep level instead of being caught by the per-agent `except` and leaving every agent silently unwakeable.
