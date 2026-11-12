### Added

- Keenable client: new `allow_public` keyword-only parameter (default `False`) on `keenable_search` and `keenable_fetch`; empty API key with public fallback off now raises `RuntimeError` without making an HTTP call
- Keenable client: `public_fallback_enabled(desktop_settings)` helper reads the `keenable_public_fallback` boolean from the `broker` preferences namespace
- Secrets app: "Allow Keenable keyless search" switch (off by default) that reads/writes the `keenable_public_fallback` preference via `GET/PUT /api/preferences/broker`, merging with existing keys