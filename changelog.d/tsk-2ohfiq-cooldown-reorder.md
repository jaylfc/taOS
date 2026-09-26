### Fixed
- LLM gateway cooldown now reorders candidates instead of excluding all cooled backends, so a single-backend model can recover from transient failures within the cooldown window
