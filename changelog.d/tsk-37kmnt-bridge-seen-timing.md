### Fixed
- Bridges (hermes, thclaws, nanobot): mark a user_message as seen only after the reply POST succeeds. Previously the message ID was added to the deduplication set before the error-cooldown check and reply POST, causing cooldown-suppressed messages and messages with failed reply POSTs to be marked processed and never retried.
