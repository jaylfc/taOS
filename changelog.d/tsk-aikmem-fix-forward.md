### Fixed
- The deferred-model self-heal deploy path now correctly clears `models_skipped` on success and preserves `taosmd_selfheal_task_id` when a stall guard triggers, preventing both duplicate multi-GB pulls and lost in-flight task pointers.
