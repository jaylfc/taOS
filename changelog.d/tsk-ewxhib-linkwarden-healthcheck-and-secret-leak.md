### Fixed

- Docker installer: a companion service that declares a healthcheck is now waited on with `depends_on: {<name>: {condition: service_healthy}}` instead of a bare `depends_on: [<name>]`, so an app such as Linkwarden no longer starts before its postgres database is READY on first boot. A postgres companion with no declared healthcheck gets a default `pg_isready` healthcheck. (#3607)

### Security

- `.gitignore` now covers the generated artefacts the DockerInstaller writes under an app dir at the repo root (`apps/*/.secret_key`, `apps/*/docker-compose.yaml`, `apps/*/.env`, `apps/*/.env.*`), so a test run whose `apps_dir` is `apps/...` can no longer leak a live 64-byte `.secret_key` into the branch via a stray `git add -A`. The runtime path `data/apps/` was already covered; this closes the repo-root path.