### Added
- Register embedding routes for OpenAI-compatible local backends (llama-cpp, lmstudio) by probing `/models` endpoint, enabling `taos-embedding-default` alias for servers with embedding models loaded. lmstudio support activates when added to providers.ALL_TYPES.
- The gateway request path now discovers llama-cpp/lmstudio embedding models, and embedding routes carry the backend api key.