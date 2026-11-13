### Added
- taOS Agent: the `kilo` CLI is a new operator-override harness (framework `kilo`, one `kilo run` per turn, `KILO_API_KEY` taken from the `kilo_api_key` secret, never chosen by `auto`).

### Fixed
- taOS Agent chat route: a `kilo` turn now builds a `KiloCliAdapter` from `ensure_taos_kilo_harness` instead of an `OpenCodeConfig(base_url=None)` adapter that crashed on `server.base_url`; the opencode server is no longer started for a kilo turn.
