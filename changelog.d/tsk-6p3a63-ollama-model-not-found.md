### Fixed

- Ollama model-not-pulled 404s now return `model_not_found` (404) with a pull hint instead of being reported as `backend_not_supported` (501).
