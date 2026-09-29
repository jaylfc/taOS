### Fixed

- Stop four test modules from writing into the repo's live `data/` folder by passing `data_dir=tmp_path` to `create_app()` instead of setting the dead `TINYAGENTOS_DATA_DIR` environment variable (which the application never reads).
- Add a permanent guard test that fails CI if any file under `tests/` or `tinyagentos/` still references `TINYAGENTOS_DATA_DIR`.
- Add an autouse session fixture in `tests/conftest.py` that snapshots `PROJECT_DIR/data` mtimes at session start and fails teardown if any file is created or modified during the run.
