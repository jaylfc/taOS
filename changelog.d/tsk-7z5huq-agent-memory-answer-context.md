### Added
- Route all agent prompt memory paths through taOSmd's `answer_context` function for unified memory context assembly with token budgets, dropped counts, and identity isolation (agent_chat_router, bridge_session, skill memory_search, user_memory agent-search, routes/memory search for agents, chat reactions).
- Identity isolation tests proving agents only receive their own and permitted memory through the new path.
- Dropped count reporting in agent traces via `memory_truncated` events.

### Fixed
- In-process `answer_context` now gets actual dropped count from taOSmd ContextAssembler instead of heuristic estimation.
- In-process path no longer double-reserves `reasoning_reserve` (was subtracted client-side, now passed to ContextAssembler like HTTP path).
- `_ensure_in_process` no longer silently masks config errors; raises on non-ImportError exceptions.
- Backward compatibility for `skill_exec` memory_search response format (includes `results` and `note` fields).
- Backward compatibility for `user_memory` agent-search response format (includes `results` field).

### Tests
- New tests for in-process dropped count calculation and reasoning_reserve handling.
- New identity isolation tests for memory access across agents.
- Updated existing tests to mock taOSmd client instead of build_context_window.