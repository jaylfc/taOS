### Fixed

- Agent memory recall now calls the real taOSmd `answer_context` API (in-process and over HTTP) instead of raw `ContextAssembler.assemble`, and the budget is computed via `taosmd.default_budget_tokens` from the model's context window, measured prompt usage, and per-model reasoning reserve. Fixes `get_taosmd_client(self._state)` raising `AttributeError` on a bare app-state object (no `.app`), which was swallowed so production agents received no memory. `reasoning_reserve` is no longer sent over HTTP (the server rejects it); it is applied only to `default_budget_tokens`.
