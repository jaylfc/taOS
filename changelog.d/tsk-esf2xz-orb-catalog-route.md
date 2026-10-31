### Added

- `tinyagentos.routes.orb`: HTTP route for signed Orb catalog (GET `/api/orb/catalog`), admin-gated, returns source (remote/cache), serial, and app list with versions and yanked flags; maps catalog signature/key/serial/state errors to 503 codes; https-only base URL with `orb.catalog_url` config.