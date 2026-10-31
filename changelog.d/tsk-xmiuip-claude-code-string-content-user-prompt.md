### Fixed

- `tinyagentos/history_import/claude_code.py`: a typed user prompt whose `message.content` is a plain string is now emitted as a single `message_in` instead of being silently dropped. Records carrying `isMeta: true` are skipped as harness injections.
