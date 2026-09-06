### Fixed

- linkwarden manifest now declares a postgres:16-alpine companion service with a persisted volume and {secret_key}-style generated password; DATABASE_URL points at the companion's service name "postgres" instead of unresolvable localhost