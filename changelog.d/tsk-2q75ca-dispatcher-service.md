### Added

- Dispatcher service implementation (Dispatcher S1/jaylfc/taOS#2141) with:
  - DispatcherService.tick_user and DispatcherService.tick methods
  - dispatcher_tick_loop background task
  - Integration with dispatcher_store, agent_heartbeat, and wake_budget
  - Board dispatch filtering based on project settings
  - Task assignment with race condition handling via assign_if_unassigned
  - Wake budget enforcement and agent wake scheduling