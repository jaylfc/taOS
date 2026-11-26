### Fixed

- In-tree agent bridges now retry a final reply with capped backoff (1, 2, 4, 8, 15 s then 15 s) inside a window of at least 180 s so a reply survives a controller restart (TimeoutStartSec=180), and the SSE loop records each id: line and resends it as Last-Event-ID on reconnect so no event position is lost. Applies to every bridge that has events_url and post_reply: hermes, deer-flow, nanobot, thclaws, langroid, openai-agents-sdk, openai_agents_sdk, pocketflow and smolagents. Streaming deltas are not retried; only final replies.
