### Fixed
- XWatchStore test fixture now closes the store after each test to avoid leaking aiosqlite worker threads.

### Added
- XWatchStore `_post_init` migration now uses a named `LEGACY_USER_ID` constant with a comment explaining the intentional retroactive ownership assignment.
