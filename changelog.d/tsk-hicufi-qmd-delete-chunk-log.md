### Fixed
- QMD /delete-chunk non-2xx responses are now logged as warnings with the status code instead of being silently swallowed; the ingest pipeline continues (delete is best-effort).