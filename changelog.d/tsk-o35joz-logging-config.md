### Fixed

- Added `logging.config.dictConfig()` in `create_app()` to configure the root logger with a StreamHandler and formatter carrying `%(asctime)s %(levelname)s %(name)s: %(message)s`. Added `TAOS_LOG_LEVEL` environment variable defaulting to INFO, which controls the root logger level.