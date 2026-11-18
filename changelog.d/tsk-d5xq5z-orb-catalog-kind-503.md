### Added

- `tinyagentos.routes.orb` / `tinyagentos/orb/catalog.py` / `tinyagentos/orb/catalog_sig.py`: map catalog kind refusals (`orb_catalog_kind_invalid`) and shape refusals (`orb_catalog_invalid`) to named 503s on GET /api/orb/catalog; kind or shape refusals from the remote fetch do not fall back to cache.
