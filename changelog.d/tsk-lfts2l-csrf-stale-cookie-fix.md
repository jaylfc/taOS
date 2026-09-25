### Fixed
- CSRF stale-cookie clear no longer deletes a fresh session cookie minted by the same response.
- `verify_csrf` now passes the request's User-Agent to `validate_session`, preventing UA-bound sessions from being misread as stale when #3120 lands.
