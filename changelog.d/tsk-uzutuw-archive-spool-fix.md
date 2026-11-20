# Fix archive spool drain event loss, duplication, and startup blocking

### Fixed
* Fix archive spool drain losing events when append-back fails - track failures and keep .draining file
* Fix crash recovery duplicating events - use .done file to track processed lines
* Fix app startup blocking on archive spool drain - run drain in background task
