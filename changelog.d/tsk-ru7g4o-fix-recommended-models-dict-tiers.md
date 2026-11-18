### Fixed

- GET /api/models/recommended now reads dict-form hardware_tiers entries ({recommended: <variant>, fallback: <variant>}) from model manifests, so recommended and compatible models are listed again instead of always being empty.
