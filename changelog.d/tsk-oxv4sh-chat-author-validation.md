### Fixed

- POST /api/chat/messages now validates author_id for session users: only "user", the caller's user_id, or missing (defaults to "user") are accepted; any other author_id returns 403 with {"error": "author_id must be the caller"}. author_type is forced to "user" for session users. Device bearers and bound agents are unchanged.