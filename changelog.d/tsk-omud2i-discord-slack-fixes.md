### Fixed

- **Discord Connector:** Honour `Retry-After` header on 429 rate limits to distinguish them from empty results, preventing sustained rate limits and potential token bans. Added per-channel backoff window tracking similar to the store_popularity.py pattern.

- **Slack Connector:** Fixed message delivery bug by moving the cursor advance to after successful message dispatch, ensuring at-most-once delivery semantics instead of losing messages when dispatch fails.

**Note:** The Discord connector now handles 429 responses correctly by respecting the `Retry-After` header and implementing backoff windows, but still uses REST polling. According to the audit documentation, a full replacement with discord.py's Gateway WebSocket is needed for true push delivery. Slack has a similar limitation with REST polling instead of SocketMode.