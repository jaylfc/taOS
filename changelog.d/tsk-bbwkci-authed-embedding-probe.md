### Fixed

- llama-cpp/lmstudio /models discovery probe now sends Authorization header when backend has api_key or api_key_secret, so key-protected servers return embedding models instead of 401.