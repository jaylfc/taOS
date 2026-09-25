### Fixed
- XWatchStore now runs on aiosqlite via BaseStore, lives under `data_dir` instead of the current working directory, scopes rows by `user_id`, and is wired onto `app.state.x_watch_store` in the app lifespan so watch endpoints are reachable.
