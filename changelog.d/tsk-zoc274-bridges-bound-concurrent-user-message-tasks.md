### Fixed
- Bound concurrent user_message tasks in the embedded bridges (hermes, thclaws, nanobot) using a module-level semaphore (_MAX_INFLIGHT=4) and tracked in-flight tasks to prevent unbounded task creation during SSE event bursts.
