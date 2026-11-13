### Fixed
- busctl() cancellation handler no longer raises UnboundLocalError when cancellation occurs before create_subprocess_exec returns; CancelledError now propagates correctly