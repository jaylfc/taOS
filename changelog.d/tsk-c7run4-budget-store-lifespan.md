### Fixed

- `AgentBudgetStore` is now constructed once in the app lifespan instead of on every request, eliminating repeated DDL execution.
- Budget route handlers (`GET/PUT/POST /api/agents/{name}/budget*`) now run sync sqlite3 calls via `asyncio.to_thread` so they no longer block the event loop.
