### Fixed

- Fixed the permanent 409 error when LiteLLM proxy is not running at deploy time. Previously, when `proxy.is_running()` returned false, the deployer would skip LLM key generation and leave `llm_key=None`, causing bootstrap to return 409 forever.

- Fixed the permanent None key after model re-scope failures. Previously, when `update_agent_model` failed to re-scope an agent's key, it would set `llm_key=None` permanently without attempting to mint a new key.

- Introduced a unified `ensure_agent_llm_key()` helper that:
  - Mints scoped keys via proxy when available
  - Applies consistent fallback policy (master key) when proxy is running but key minting fails
  - Logs appropriate warnings and records steps
  - Handles the proxy-not-running scenario with proper warnings

- Updated the bootstrap endpoint to call the helper before returning 409, preventing permanent error loops

- Tests now cover:
  - Running proxy with successful key minting
  - Running proxy with key mint failure (refusal paths)
  - Proxy not running with deferred key generation
  - Running proxy in routing-only mode with master key fallback
  - Running proxy with DB configured but mint failure (refusal)
