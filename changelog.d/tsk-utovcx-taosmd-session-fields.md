### Added

- **taOSmd conversation turns carry session_id, turn_index, session_date** - the chat route (tinyagentos/routes/chat.py:post_message) now writes each archived conversation turn with the conversation id, the turn's 0-based monotonic index, and the turn's own timestamp (ISO 8601 UTC) so taOSmd's answer_context can widen a memory hit into its neighbouring turns and date it correctly.
