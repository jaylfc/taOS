### Fixed

- `knowledge_ingest._summarise` and `knowledge_categories._llm_categorise` now POST to the OpenAI-compatible `/v1/chat/completions` endpoint instead of the unserved `/generate` and bare base-URL paths. LLM failures surface the item status as `partial` with the error stored in metadata rather than silently marking it `ready`.
