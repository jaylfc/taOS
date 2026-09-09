### Fixed

- Moved logging configuration out of `create_app()` into an idempotent `configure_logging()` in `tinyagentos/logging_config.py`, called once from the server entrypoint. `create_app()` no longer replaces the root logger's handlers, so pytest's `caplog` and host-installed handlers are preserved across factory calls.
