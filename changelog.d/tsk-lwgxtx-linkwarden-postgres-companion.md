### Fixed

- Linkwarden now ships a companion Postgres service in its compose manifest, fixing the boot failure caused by `DATABASE_URL` pointing at a non-existent `localhost:5432`.
