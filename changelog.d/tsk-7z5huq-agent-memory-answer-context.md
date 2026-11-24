### Added
- Route all agent memory context paths through taOSmd `answer_context` for unified token-budgeted context assembly with dropped-item visibility.
  - `agent_chat_router.py`: Per-agent chat history context for bridge/ACP agents now uses `answer_context` with budget derived from each agent's own model window (share, not whole window). Canonical identity (`registry_canonical_id`) passed to taOSmd.
  - `routes/memory.py`: Agent callers (bound local token, registry JWT) use `answer_context` for `/api/memory/search`; human callers keep raw qmd search. No silent fallback on failure.
  - `routes/user_memory.py`: Agent-search endpoint (`/api/user-memory/agent-search`) uses `answer_context` for agents with `can_read_user_memory`. No silent fallback on failure.
  - `taosmd_client.py`: New client wrapper for in-process and HTTP `answer_context` calls with `AnswerContextResult` dataclass. In-process mode now reports `dropped` counts. Consistent `reasoning_reserve` handling between modes.
- Surface `dropped` counts in agent trace so truncation is visible for debugging.
- Preserve raw qmd/SQLite search for Memory app UI and CLI (non-agent callers).

### Fixed
- Context budget calculation now uses a share (~50%) of each agent's own model context window, per taOSmd spec.
- In-process `answer_context` now passes `reasoning_reserve` to ContextAssembler instead of subtracting locally, matching HTTP mode behavior.
- Agent identity passed to taOSmd is the canonical `registry_canonical_id` (not config name), preventing cross-agent memory leaks.
