### Fixed
- Agent chat reply route now returns 503 (chat store unavailable) when channel lookup raises, and 400 (invalid channel_id) when channel_id is not a string, instead of unhandled 500 errors.