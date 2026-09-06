### Security
- DockerInstaller now substitutes the per-app `{secret_key}` placeholder in
  `install.env` values (not just `config_files` content), so Linkwarden's
  `NEXTAUTH_SECRET` is a stable 64-hex-char secret persisted in
  `<app_dir>/.secret_key` instead of the shipped default. Previously every host
  ran Linkwarden with the publicly-known session-signing secret `changeme`,
  allowing session forgery.
- Linkwarden manifest restores the required `DATABASE_URL: "postgresql://postgres:postgres@localhost:5432/linkwarden"`.
  Database is now explicitly declared in the manifest to match upstream Linkwarden.
- Generated docker-compose.yaml and config files are now written with permissions 0o600
  to protect any secret substitutions (previous default umask 0o644 exposed secrets
  in the live session-signing key). Applies to all files written by
  `_write_config_files` and `install` that contain a `{secret_key}` substitution.

### Fixed
- Fix-forward #2816: restore linkwarden DATABASE_URL (false SQLite premise) and chmod 0600 the generated docker-compose.yml that now carries the real secret
