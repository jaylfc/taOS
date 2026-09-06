### Security
- DockerInstaller now substitutes the per-app `{secret_key}` placeholder in
  `install.env` values (not just `config_files` content), so Linkwarden's
  `NEXTAUTH_SECRET` is a stable 64-hex-char secret persisted in
  `<app_dir>/.secret_key` instead of the shipped default. Previously every host
  ran Linkwarden with the publicly-known session-signing secret `changeme`,
  allowing session forgery.
- Linkwarden manifest drops the unused `DATABASE_URL` (no Postgres companion is
  started) and sets `NEXTAUTH_SECRET: "{secret_key}"`.
