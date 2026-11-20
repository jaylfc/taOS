### Fixed
- Restored the chat deduplication guard in `_handle_reply` so a retried final reply with the same id does not write the chat message twice; the archive write (with its own dedup set) runs before the early return, so a retry after a chat failure still archives exactly once.
- Bounded the per-session `_archive_seen_ids` set (now an `OrderedDict`) to the same 256-entry cap as `_seen_final_ids`, preventing unbounded growth.
- Clarified that replies without an id are not deduplicated in either the archive or chat paths.
- Fixed `test_final_reply_idempotent_archive_then_chat_failure_then_success` to actually exercise a chat-store failure on the first `send_message` call and verify two delivery attempts, one archive record, and one chat message.
