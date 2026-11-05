### Fixed

- Orb catalog route (GET /api/orb/catalog) returns named 503 errors: orb_catalog_kind_invalid when fetched catalog has wrong kind (not firmware), and orb_catalog_invalid when catalog is not a JSON object or expires is not ISO 8601, instead of generic OrbError.