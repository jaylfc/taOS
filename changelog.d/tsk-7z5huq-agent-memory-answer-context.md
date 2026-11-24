### Added
- Route all agent memory context paths through taOSmd `answer_context` for unified token-budgeted context assembly with dropped-item visibility.
  - `agent_chat_router.py`: Chat history context for bridge/ACP agents now uses `answer_context` with budget derived from smallest recipient model window (share, not whole window).
  - `routes/memory.py`: Agent callers (bound local token, registry JWT) use `answer_context` for `/api/memory/search`; human callers keep raw qmd search.
  - `routes/user_memory.py`: Agent-search endpoint (`/api/user-memory/agent-search`) uses `answer_context` for agents with `can_read_user_memory`.
  - `bridge_session.py`: Trace events (`message_in`, `message_out`) now include `answer_context` metadata (dropped count, budget, layers, intent).
  - `taosmd_client.py`: New client wrapper for in-process and HTTP `answer_context` calls with `AnswerContextResult` dataclass.
- Surface `dropped` counts in agent trace so truncation is visible for debugging.
- Preserve raw qmd/SQLite search for Memory app UI and CLI (non-agent callers).

### Fixed
- Context budget calculation now uses a share (~50%) of the smallest known model context window among recipients, per taOSmd spec.
- Unknown-window recipients cap the shared budget at the historical 4000-token default to prevent overflow.
