### Added

- `tinyagentos/history_import/claude_code.py`: pure converter that reads a Claude Code session `.jsonl` transcript and produces Envelope v1 events (`message_in`, `message_out`, `tool_call`, `tool_result`) plus Tier 1 index lines. Thinking blocks are dropped, image bytes are replaced with a placeholder, secret-file reads are omitted via an injected predicate, and every text field passes through an injected redactor.
