### Added

- Engine selection support for BaseStore: Added Engine enum (SQLITE, POSTGRES) to allow stores to specify which database engine to use. Stores now have an optional `engine` parameter in their constructor and an `ENGINE` class attribute, defaulting to SQLite for backward compatibility. Added `_init_sqlite()` and `_init_postgres()` methods to BaseStore to handle engine-specific initialization.

### Changed

- BaseStore API: Modified `BaseStore.__init__()` to accept optional `engine` parameter. Store subclasses can now specify `ENGINE = Engine.POSTGRES` if they intend to use Postgres in future migration slices.

### Migration notes

- **Backward compatibility:** SQLite remains the default engine. No existing stores will change behavior unless they explicitly set `ENGINE = Engine.POSTGRES`.
- **Future migration:** This change provides the foundation for migrating stores to Postgres in follow-on slices (tsk-wdplga migration 2+). The engine selection logic is now in place and tested.