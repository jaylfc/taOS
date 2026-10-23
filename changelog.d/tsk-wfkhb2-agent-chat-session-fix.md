### Fixed

- Agent chat session reply endpoint now enforces channel membership: a registry agent can only reply in channels where its slug is listed as a member. Requests targeting a foreign channel (via `channel_id` or `trace_id`) return 403 `{"error": "not a member of this channel"}`.
- Registry agent slug collision prevention: when two active registry agents have canonical IDs that strip to the same slug, only the first-registered agent may use the session; subsequent agents receive 403 `{"error": "handle collides with another registry agent"}`.