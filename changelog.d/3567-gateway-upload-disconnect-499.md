### Fixed

- LLM gateway audio routes (`/api/llm/v1/audio/transcriptions` and `/audio/speech`): a client that disconnects mid-upload now gets a 499 and the voice daemon is never contacted, instead of an unhandled `ClientDisconnect`.
