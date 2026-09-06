### Fixed

- **Agent-as-a-Model multi-turn context**: `POST /v1/chat/completions` now forwards the full conversation history (not just the last user message) to the agent turn driver, so multi-turn conversations are no longer silently dropped on each request (qodo bug 2).
