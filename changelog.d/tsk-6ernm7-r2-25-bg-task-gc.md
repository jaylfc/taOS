### Fixed
- Fire-and-forget asyncio tasks in `ClusterManager` and the `/api/agents` deploy
  route are now kept alive via a new `_spawn_background_task` helper that holds
  a strong reference in `_background_tasks` (asyncio recommended pattern).  Tasks
  created inline without a reference can be garbage-collected mid-flight, which
  previously left agents stuck in `"deploying"` forever.  The done-callback now
  logs exceptions instead of silently discarding them.
