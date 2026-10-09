### Added

- DispatcherService class with `tick_user()` and `tick()` methods wired to the MERGED dispatch primitives (tsk-jlkqfa part 2)

### Fixed

- DispatcherService properly checks board dispatchability using `is_board_dispatchable()`
- DispatcherService respects wake budgets when waking agents
- DispatcherService handles race conditions between select and assign operations
- DispatcherService works correctly when heartbeat is disabled but wake budget is respected
- DispatcherService properly filters manually claimed tasks from dispatch