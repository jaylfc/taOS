### Fixed

- AgentRegistryStore now serialises every write-and-commit sequence through `self._db` with an `asyncio.Lock`, so a concurrent `rollback()` from one coroutine can no longer discard another coroutine's uncommitted row.
