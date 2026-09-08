### Fixed

- `apply_wal_pragmas_async` now sets `busy_timeout = 5000`, matching the sync helper. The three ad-hoc re-issues in `agent_budget_store`, `litellm_keystore`, and `broker/store` are removed in favour of the shared helper.
