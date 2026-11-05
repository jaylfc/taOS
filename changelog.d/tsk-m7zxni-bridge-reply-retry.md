### Fixed

- Bridges retry the reply POST up to 3 times (1.0s * attempt backoff) on
  `httpx.TransportError` and 5xx/429 responses, so an agent's reply is no
  longer dropped when the POST fails once. Only 2xx is treated as success;
  other 4xx are not retried. Affected: hermes, nanobot, thclaws, langroid,
  pocketflow, smolagents, openai-agents-sdk, openai_agents_sdk, deer-flow.
- DeerFlow's SSE loop now caps `pending_tasks` at `_MAX_INFLIGHT * 4`:
  when the cap is reached it waits for a running task to finish before
  scheduling the next `user_message`, so a burst cannot queue unbounded tasks.
