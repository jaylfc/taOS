### Added
- Guest chat sessions (registry agents with the `chat_session` grant) now persist undelivered messages in a durable outbox, so a controller restart no longer drops them; `GET /api/agents/self/chat/events` tags each message with an SSE `id` and resumes from `Last-Event-ID` (or `?since=`), replaying only newer messages in order before going live.
