### Added

- Keenable client: new `allow_public` keyword-only parameter (default `False`) on `keenable_search` and `keenable_fetch`; empty API key with public fallback off now raises `RuntimeError` without making an HTTP call
- Keenable client: `public_fallback_enabled(desktop_settings)` helper reads the `keenable_public_fallback` boolean from the `broker` preferences namespace
- Secrets app: "Allow Keenable keyless search" switch (off by default) that reads/writes the `keenable_public_fallback` preference via `GET/PUT /api/preferences/broker`, merging with existing keys

### Fixed

- Secrets app: Keenable toggle now confirms its save before updating the switch, shows an inline error on failure, disables the switch while saving to prevent races, and avoids wiping other broker prefs if the merge GET fails
- Keenable client: `public_fallback_enabled` now returns `True` only for the boolean `True` value (not for truthy strings or numbers)
