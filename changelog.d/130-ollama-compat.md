### Added

- **Ollama API compatibility (#130)** — Ollama clients (Open WebUI, the Ollama CLI, ...) can now point at the taOS controller with the same consent key as the OpenAI-compatible surface (#129): `GET /api/tags` lists the agents the key is consented for in Ollama's format, and `POST /api/chat` / `POST /api/generate` drive one agent turn and answer in Ollama's shapes, including NDJSON streaming (Ollama's default). `/api/pull` and `/api/delete` are not part of this slice — they mutate the catalog and are flagged as follow-ups.
