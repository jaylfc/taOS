### Fixed
- Bridge session final reply recovery now scopes lookup by agent author to prevent cross-agent message corruption in group channels
- Recovery path no longer duplicates content_blocks on retry (idempotent by block id/content)
- Recovery path now broadcasts message_edit and message_state events so clients see replies whose first attempt failed before broadcast
- Streaming placeholder now writes reply_id to metadata before first edit, preventing duplicate inserts on retry after mid-edit failure
- Third and subsequent retries of the same reply_id are now no-ops (no extra broadcasts or changes)
