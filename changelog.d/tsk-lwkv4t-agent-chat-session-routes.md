### Added

- Self-only chat session routes `GET /api/agents/self/chat/events` and `POST /api/agents/self/chat/reply` for registry agents holding the new `chat_session` scope; the session slug comes only from the token and a handle that collides with a local agent is refused (tsk-lwkv4t).
