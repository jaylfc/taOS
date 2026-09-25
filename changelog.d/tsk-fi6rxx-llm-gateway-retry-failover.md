### Added
- LLM gateway retries on connect error, timeout, and upstream 5xx across backends serving the same model, with a total attempt cap, overall deadline, and per-backend cooldown
