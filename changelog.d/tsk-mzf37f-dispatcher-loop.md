### Added
- tsk-mzf37f: add module-level dispatcher_tick_loop with per-user locks and poll_seconds gating; wire DispatcherService + supervised loop into app lifespan next to agent_heartbeat_loop (with asyncio import fix for cancellation).
