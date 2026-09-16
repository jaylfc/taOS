### Fixed

- Fixed Postgres opt-in footgun: `.litellm_db_url` no longer disables the working SQLite keystore
  (`inhouse_keys` is now independent of `db_url` and remains authoritative for LiteLLM per-agent keys)
- `litellm_migrate.migrate()` now logs a warning and returns `"postgres-configured"` status instead of raising `RuntimeError`
- `llm_proxy.py` no longer exports `DATABASE_URL` into the LiteLLM subprocess, preventing Prisma mode activation
- Database keystore is now authoritative for LiteLLM per-agent keys regardless of Postgres configuration

### Added

- Added `test_pg_red.py` test suite that verifies Postgres opt-in no longer hard-fails
- Escape hatches `.litellm_force_inhouse_keys` and `.litellm_disable_inhouse_keys` remain functional
- Updated documentation to clarify Postgres is only used as a first-class app database for other purposes