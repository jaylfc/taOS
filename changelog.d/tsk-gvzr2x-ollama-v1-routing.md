### Added
- The `/api/llm/v1` gateway now forwards ollama and rkllama model requests to the upstream's `/v1/chat/completions` endpoint instead of returning 501, reusing the existing retry, streaming, and usage recording code.
