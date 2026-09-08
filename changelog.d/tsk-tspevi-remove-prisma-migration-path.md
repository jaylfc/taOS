### Fixed
- **R2-18 remove LiteLLM prisma/Postgres migration path**: `litellm_migrate` no longer shells out to `prisma generate` at runtime. When `DATABASE_URL` is configured the module raises a clear "not supported" error; without it the proxy starts cleanly with no prisma package installed. The `prisma>=0.11.0` dependency is removed from the `proxy` extra.
