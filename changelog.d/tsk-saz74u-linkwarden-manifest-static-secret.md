### Fixed

- Linkwarden manifest: replaced static `NEXTAUTH_SECRET: "changeme"` with `{secret_key}` placeholder per-install; removed `DATABASE_URL` since no Postgres companion service is started in single-container installs