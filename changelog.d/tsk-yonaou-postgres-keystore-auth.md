### Fixed
- Postgres opt-in no longer disables the SQLite keystore or hard-fails at startup. `.litellm_db_url` is now ignored for LiteLLM virtual keys; the keystore remains authoritative.
- `litellm_migrate.migrate()` logs a warning and returns a status string when `.litellm_db_url` is present, instead of raising `RuntimeError`.
- LiteLLM subprocess no longer receives `DATABASE_URL`, preventing prisma startup attempts.
- Any app manifest can now declare a Postgres companion using the `postgres:16-alpine` pattern with persisted volumes and `{secret_key}`-generated passwords.
