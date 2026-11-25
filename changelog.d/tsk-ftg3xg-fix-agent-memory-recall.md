### Fixed
- Agent memory recall now uses the real taOSmd `answer_context` API (in-process and HTTP) with proper token budget calculation via `default_budget_tokens`, reasoning reserve per model recipe, and agent-isolated memory stores.
- `get_taosmd_client` fixed to work with both FastAPI Request and app.state directly.
- HTTP `answer_context` calls no longer send `reasoning_reserve` (server rejects it with 400).
- Budget calculation uses measured used tokens (system prompt + tools + conversation) and model-specific reasoning reserve.
