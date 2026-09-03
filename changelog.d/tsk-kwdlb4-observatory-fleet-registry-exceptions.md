### Fixed
- Broadened registry exception handling in `GET /api/observatory/fleet` so that any registry failure is logged and gracefully skipped instead of propagating as a 500 error.
