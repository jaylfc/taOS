### Fixed
- Postgres companion without `env` key now receives default healthcheck (was incorrectly nested inside `if "env" in comp:` block)