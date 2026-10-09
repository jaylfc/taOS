### Fixed
- Fixed default postgres healthcheck only being applied to the last companion in a list.
- Changed postgres healthcheck to use `pg_isready -h 127.0.0.1` to probe TCP connections instead of Unix socket.
- Added `_healthcheck_enabled` helper to correctly ignore disabled healthchecks (including `{"disable": True}` and `{"test": ["NONE"]}`) in depends_on logic, preventing indefinite waiting.